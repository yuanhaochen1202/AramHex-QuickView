"""Read only the current game's loading roster from its local r3d log.

No LCU connection, process memory, command line or player-history requests.
Player names and identifiers are never retained in the parsed state or logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import heapq
import json
import logging
import math
import os
from pathlib import Path
import re
import time

from .window import GameProcess


MAX_READ = 64 * 1024
MAX_LINE = 8192
DISCOVER_INTERVAL = 1.0
# Only the initial startup's filenames qualify, not an arbitrary "latest game".
LOG_START_WINDOW = range(-1, 31)
_PREFIX = r"^[0-9]+\.[0-9]+\|\s*(?:ALWAYS|INFO)\|\s*"
_ROSTER = re.compile(
    _PREFIX + r'ROST\|\s*CONNECTION READY\s*\|\s*Team(Order|Chaos)\s+(\d{1,2})\)\s*'
    r'''(?:"[^\r\n]{0,256}"|'[^\r\n]{0,256}')\s*(\*\*LOCAL\*\*)?\s*-\s*Champion\(([A-Za-z0-9_]{1,64})\)'''
    r'\s+SkinID\(\d+\)\s+TeamBuilderRole\([A-Za-z_]+\)\s+PUUID\([^\r\n)]*\)'
    r'\s+ConnectionState\([A-Za-z]+\)\s*$'
)
_FLOW = re.compile(_PREFIX + r"FLOW\|\s*(.*?)\s*$")
_GAME_ID = re.compile(_PREFIX + r"GameStartData::\s*GameID\s*=\s*(\d{1,20})\s*$")
_CLIENT_SESSION = re.compile(
    r"^([0-9]+\.[0-9]+)\|\s*OKAY\|\s*rcp-be-lol-champ-select\|"
    r"\s*/lol-champ-select/v1/session:\s*(\{.*\})\s*$"
)
_CLIENT_FILE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d)_\d+_LeagueClient\.log$")
CLIENT_TAIL_BYTES = 512 * 1024
TARGET_QUEUE = 2400
CUSTOM_QUEUE = 3270
SUPPORTED_QUEUES = frozenset((TARGET_QUEUE, CUSTOM_QUEUE))
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadingMember:
    side: int
    slot: int
    champion: str
    is_local: bool


@dataclass(frozen=True)
class LoadingRoster:
    game_key: str
    members: tuple[LoadingMember, ...]
    complete: bool


@dataclass(frozen=True)
class QueueEvidence:
    queue_id: int | None = None
    conflict: bool = False


class LoadingParser:
    def __init__(self):
        self.members: dict[tuple[int, int], LoadingMember] = {}
        self.active = False
        self.finished = False
        self.unsupported = False
        self.conflicted = False
        self.game_id: str | None = None
        self._seen_slots: dict[tuple[int, int], str] = {}
        self._local_members: dict[tuple[int, int, str], str] = {}

    def feed(self, line: str) -> None:
        match = _GAME_ID.fullmatch(line)
        if match:
            if self.game_id is not None and self.game_id != match[1]:
                self.conflicted = True
            else:
                self.game_id = match[1]
            return
        match = _ROSTER.fullmatch(line)
        if match:
            side = 100 if match[1] == "Order" else 200
            slot = int(match[2])
            if not 0 <= slot < 5:
                self.unsupported = True
                return
            champion = match[4]
            previous = self._seen_slots.get((side, slot))
            if previous is not None and previous != champion.casefold():
                self.conflicted = True
            self._seen_slots[side, slot] = champion.casefold()
            member = LoadingMember(side, slot, champion, bool(match[3]))
            if member.is_local:
                # Only game-side, slot and champion are retained, never the player's identity.
                key = (side, slot, champion.casefold())
                if self._local_members and key not in self._local_members:
                    self.conflicted = True
                else:
                    self._local_members[key] = champion
            if not self.finished:
                self.members[side, slot] = member
            return
        if self.finished:
            return
        match = _FLOW.fullmatch(line)
        if not match:
            return
        event = match[1]
        if re.fullmatch(r"Completed\s+State Transition: Popping LoadingScreen Current Topmost State: Gameplay", event):
            self.active = False
            self.finished = True
            self.members.clear()
        elif re.fullmatch(r"(?:Starting|Completed)\s+State Transition: Pushing LoadingScreen Current Topmost State: (?:Gameplay|LoadingScreen)", event):
            self.active = True
        # "Loading Ended" only means this machine finished loading; other players may still be loading.

    def snapshot(self, game_key: str) -> LoadingRoster | None:
        # The exact game's mode is checked by the reader; team size is not a mode detector.
        if not self.active or self.finished or self.unsupported or self.conflicted or not self.members:
            return None
        complete = set(self.members) == {(side, slot) for side in (100, 200) for slot in range(5)}
        return LoadingRoster(game_key, tuple(self.members[k] for k in sorted(self.members)), complete)

    def local_champion(self) -> str | None:
        if self.unsupported or self.conflicted or len(self._local_members) != 1:
            return None
        return next(iter(self._local_members.values()))


class LoadingRosterReader:
    """A bounded incremental tail, scoped to executable path AND process creation time."""

    def __init__(self):
        self.process: GameProcess | None = None
        self.path: Path | None = None
        self.parser = LoadingParser()
        self.offset = 0
        self.pending = b""
        self.discard_line = False
        self.file_identity = None
        self._next_discovery = 0.0
        self._next_mode_check = 0.0
        self._queue_id: int | None = None
        self._mode_conflicted = False
        self._caught_up = False
        self._reported = None

    def reset(self, process: GameProcess | None = None) -> None:
        self.process = process
        self.path = None
        self._clear_file()
        self._next_discovery = 0.0

    def _clear_file(self) -> None:
        self.parser = LoadingParser()
        self.offset = 0
        self.pending = b""
        self.discard_line = False
        self.file_identity = None
        self._next_mode_check = 0.0
        self._queue_id = None
        self._mode_conflicted = False
        self._caught_up = False
        self._reported = None

    @staticmethod
    def _birth_time(stat) -> float | None:
        return getattr(stat, "st_birthtime", stat.st_ctime if os.name == "nt" else None)

    def _find_log(self, process: GameProcess) -> Path | None:
        candidates = []
        base = math.floor(process.created_at)
        # Support both Game/Logs and <installation>/Logs layouts.
        # Fixed 64 path checks, no full-disk search or enumeration of old game logs.
        for base_folder in (process.executable.parent, process.executable.parent.parent):
            root = base_folder / "Logs" / "GameLogs"
            for delta in LOG_START_WINDOW:
                stamp = datetime.fromtimestamp(base + delta).strftime("%Y-%m-%dT%H-%M-%S")
                path = root / stamp / f"{stamp}_r3dlog.txt"
                try:
                    stat = path.stat()
                    birth = self._birth_time(stat)
                    if birth is not None and birth < process.created_at - .25:
                        continue
                    if not path.is_file():
                        continue
                    candidates.append((abs(base + delta - process.created_at), path))
                except OSError:
                    continue
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def poll(self, process: GameProcess | None) -> LoadingRoster | None:
        started = time.perf_counter()
        self._caught_up = False
        if process != self.process:
            self.reset(process)
        if process is None:
            return None
        if self.path is None:
            now = time.monotonic()
            if now < self._next_discovery:
                return None
            self._next_discovery = now + DISCOVER_INTERVAL
            self.path = self._find_log(process)
            if self.path is None:
                return None
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino, self._birth_time(stat))
                if self.file_identity != identity or stat.st_size < self.offset:
                    self._clear_file()
                    self.file_identity = identity
                stream.seek(self.offset)
                chunk = stream.read(MAX_READ)
                self.offset += len(chunk)
                self._feed(chunk)
                # Catch up before publishing: a late-started assistant must consume the exit marker too.
                if self.offset < os.fstat(stream.fileno()).st_size:
                    return None
                self._caught_up = True
                roster = self.parser.snapshot(process.key)
                if (self.parser.game_id and not self.parser.conflicted and not self._mode_conflicted
                        and time.monotonic() >= self._next_mode_check):
                    self._next_mode_check = time.monotonic() + DISCOVER_INTERVAL
                    evidence = queue_evidence_for_game(process, self.parser.game_id)
                    if evidence.conflict or (self._queue_id is not None and evidence.queue_id is not None
                                             and self._queue_id != evidence.queue_id):
                        self._mode_conflicted = True
                        self._queue_id = None
                    elif evidence.queue_id is not None:
                        self._queue_id = evidence.queue_id
                    # A record falling out of the bounded tail is not contrary evidence.
                state = (self.parser.active, self.parser.finished, len(self.parser.members), self._queue_id)
                if state != self._reported:
                    self._reported = state
                    log.info("加载名单：pid=%d loading=%s ended=%s 条目=%d queue=%s 本轮读取=%.1f毫秒",
                             process.pid, *state, (time.perf_counter() - started) * 1000)
                return roster if self._queue_id in SUPPORTED_QUEUES else None
        except OSError:
            self.path = None
            self._clear_file()
            return None

    def local_champion(self) -> str | None:
        """Return only the current, mode-confirmed local champion, including after loading.

        The caller must still validate that the game's current process is this reader's
        process. A new process, unreadable/replaced/truncated log or pending backlog
        cannot reuse a previous answer.
        """
        if (self.process is None or self.path is None or not self._caught_up
                or self._queue_id not in SUPPORTED_QUEUES):
            return None
        return self.parser.local_champion()

    def _feed(self, chunk: bytes) -> None:
        if self.discard_line:
            end = chunk.find(b"\n")
            if end < 0:
                return
            chunk = chunk[end + 1:]
            self.discard_line = False
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            if len(line) <= MAX_LINE:
                # Unrelated lines are immediately discarded; no raw game log is copied or logged.
                self.parser.feed(line.decode("utf-8", errors="replace").rstrip("\r"))
        if len(self.pending) > MAX_LINE:
            self.pending = b""
            self.discard_line = True


def queue_evidence_for_game(process: GameProcess, game_id: str) -> QueueEvidence:
    """Confirm the queue using an exact GameID in recent local client-session records.

    At most three 512 KiB tails are read. Only the champ-select session endpoint is
    considered, within ten minutes before this process started; no credentials or
    player details are retained, emitted, or sent to a service.
    """
    def candidates():
        for base_folder in (process.executable.parent, process.executable.parent.parent):
            try:
                for path in (base_folder / "Logs" / "LeagueClient Logs").iterdir():
                    if _CLIENT_FILE.fullmatch(path.name):
                        yield path
            except OSError:
                continue
    paths = heapq.nlargest(3, candidates(), key=lambda p: p.name)
    found: set[int] = set()
    invalid = False
    for path in paths:
        try:
            start = datetime.strptime(_CLIENT_FILE.fullmatch(path.name)[1], "%Y-%m-%dT%H-%M-%S").timestamp()
            if start > process.created_at + 10:
                continue
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                stream.seek(max(0, size - CLIENT_TAIL_BYTES))
                data = stream.read(CLIENT_TAIL_BYTES)
            if size > CLIENT_TAIL_BYTES:
                data = data.partition(b"\n")[2]  # The first record may have been split by seeking.
            for raw in data.split(b"\n")[:-1]:
                if len(raw) > 128 * 1024 or b"/lol-champ-select/v1/session:" not in raw:
                    continue
                match = _CLIENT_SESSION.fullmatch(raw.decode("utf-8", errors="replace").rstrip("\r"))
                if not match or not process.created_at - 600 <= start + float(match[1]) <= process.created_at + 10:
                    continue
                try:
                    obj = json.loads(match[2])
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if str(obj.get("gameId", "")) != game_id:
                    continue
                queue = obj.get("queueId")
                if isinstance(queue, int) and not isinstance(queue, bool) and queue > 0:
                    found.add(queue)
                    # Locally verified Hex ARAM custom games; do not generalize to all custom games.
                    if queue == CUSTOM_QUEUE and obj.get("isCustomGame") is not True:
                        invalid = True
                else:
                    invalid = True
        except (OSError, ValueError, TypeError):
            continue
    if invalid or len(found) > 1:
        return QueueEvidence(conflict=True)
    return QueueEvidence(next(iter(found))) if found else QueueEvidence()


def queue_for_game(process: GameProcess, game_id: str) -> int | None:
    """Compatibility helper; the reader uses three-state evidence to distinguish absence."""
    return queue_evidence_for_game(process, game_id).queue_id
