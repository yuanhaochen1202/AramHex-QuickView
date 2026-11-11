"""Local-log loading roster: sanitized historical format, no game/client requests."""
from datetime import datetime
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision import loading, window
from lolhex.vision.loading import LoadingMember, LoadingParser, LoadingRoster, LoadingRosterReader
from lolhex.vision.window import GameProcess


# Structural excerpt from 2026-11-06 22:55:54. Names and IDs are fabricated.
CHAMPIONS = ("Olaf", "Blitzcrank", "Amumu", "Mel", "Gragas",
             "Malzahar", "Ahri", "TwistedFate", "XinZhao", "Vladimir")
GAME_ID = "123456789"
START = "000002.352| ALWAYS|  FLOW| Completed  State Transition: Pushing LoadingScreen Current Topmost State: LoadingScreen\n"
LOCAL_LOADED = "000003.806| ALWAYS|  FLOW| Loading Ended\n"
EXIT = "000064.374| ALWAYS|  FLOW| Completed  State Transition: Popping LoadingScreen Current Topmost State: Gameplay\n"


def roster_text(count=10, local=True):
    text = f"000000.547| ALWAYS| GameStartData::GameID={GAME_ID}\n"
    for i, champion in enumerate(CHAMPIONS[:count]):
        side, slot = ("Order", i) if i < 5 else ("Chaos", i - 5)
        marker = " **LOCAL**" if local and i == 4 else ""
        text += (f'000001.843| ALWAYS|  ROST| CONNECTION READY | Team{side} {slot}) '
                 f"'redacted-player'{marker} - Champion({champion}) SkinID(0) TeamBuilderRole(NONE) "
                 'PUUID() ConnectionState(Connected)\n')
    return text


def make_session(tmp_path, *, queue=2400, game_id=GAME_ID, pid=123, created=None, custom=None):
    created = float(int(time.time()) - 3) if created is None else created
    process = GameProcess(pid, tmp_path / "Game" / "League of Legends.exe", created, int(created * 10_000_000))
    stamp = datetime.fromtimestamp(created).strftime("%Y-%m-%dT%H-%M-%S")
    log = process.executable.parent / "Logs" / "GameLogs" / stamp / f"{stamp}_r3dlog.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    client_stamp = datetime.fromtimestamp(created - 100).strftime("%Y-%m-%dT%H-%M-%S")
    client = process.executable.parent / "Logs" / "LeagueClient Logs" / f"{client_stamp}_77_LeagueClient.log"
    client.parent.mkdir(parents=True, exist_ok=True)
    payload = {"gameId": int(game_id), "queueId": queue}
    if custom is not None:
        payload["isCustomGame"] = custom
    client.write_text('000090.000|   OKAY|    rcp-be-lol-champ-select| /lol-champ-select/v1/session: '
                      + json.dumps(payload) + '\n', encoding="utf-8")
    return process, log, client


def parse(text):
    p = LoadingParser()
    for line in text.splitlines():
        p.feed(line)
    return p


def test_sanitized_historical_roster_and_wait_for_other_players():
    p = parse(roster_text() + START + LOCAL_LOADED)
    got = p.snapshot("test-game")
    assert got.complete and len(got.members) == 10
    assert got.members[4] == LoadingMember(100, 4, "Gragas", True)
    assert all("redacted-player" not in repr(member) for member in got.members)
    assert p.game_id == GAME_ID and p.active
    p.feed(EXIT.strip())
    assert p.snapshot("test-game") is None and not p.members
    p.feed(START.strip())  # Late duplicate state cannot resurrect an ended game.
    assert p.snapshot("test-game") is None


def test_loading_publishes_each_known_member_without_using_team_size_as_mode():
    assert parse(roster_text()).snapshot("g") is None
    for count in (1, 2, 9):
        result = parse(roster_text(count) + START).snapshot("g")
        assert len(result.members) == count and result.complete is False
    six = roster_text().splitlines()
    six = [line for line in six if not any(f" {n})" in line for n in (3, 4))]
    result = parse("\n".join(six) + "\n" + START).snapshot("g")
    assert len(result.members) == 6 and not result.complete
    p = parse(roster_text() + START)
    p.feed(roster_text().splitlines()[1].replace("TeamOrder 0)", "TeamOrder 9)"))
    assert p.snapshot("g") is None


def test_queue_is_checked_before_loading_or_any_roster_entry(tmp_path, monkeypatch):
    proc, path, _ = make_session(tmp_path)
    path.write_text(roster_text(0), encoding="utf-8")
    calls = []
    monkeypatch.setattr(loading, "queue_evidence_for_game",
                        lambda p, game_id: calls.append((p, game_id)) or loading.QueueEvidence(2400))
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None
    assert calls == [(proc, GAME_ID)]
    assert reader.local_champion() is None
    with path.open("a", encoding="utf-8") as stream:
        stream.write(roster_text(1) + START)
    got = reader.poll(proc)
    assert len(got.members) == 1 and not got.complete


def test_tail_waits_for_complete_line_then_hides_on_exit(tmp_path):
    proc, path, _ = make_session(tmp_path)
    path.write_bytes((roster_text() + START[:-1]).encode())
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None
    with path.open("ab") as f:
        f.write(b"\n")
    assert reader.poll(proc).game_key == proc.key
    with path.open("ab") as f:
        f.write(LOCAL_LOADED.encode())
    assert reader.poll(proc) is not None
    with path.open("ab") as f:
        f.write(EXIT.encode())
    assert reader.poll(proc) is None
    assert reader.poll(None) is None and reader.process is None


def test_process_reuse_never_reuses_old_roster(tmp_path):
    proc, path, _ = make_session(tmp_path)
    path.write_text(roster_text() + START, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is not None
    # Same PID / executable, new creation timestamp; no matching new log yet.
    later = GameProcess(proc.pid, proc.executable, proc.created_at + 120, proc.creation_ticks + 1200000000)
    assert reader.poll(later) is None
    assert reader.parser.members == {}
    assert reader.local_champion() is None


def test_no_log_and_unreadable_or_deleted_file_clear_state(tmp_path):
    proc, path, _ = make_session(tmp_path)
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None
    path.write_text(roster_text() + START, encoding="utf-8")
    reader._next_discovery = 0
    assert reader.poll(proc) is not None
    assert reader.local_champion() == "Gragas"
    path.unlink()
    assert reader.poll(proc) is None and reader.parser.members == {}
    assert reader.local_champion() is None


def test_installation_root_log_layout_is_discovered_without_fixed_drive(tmp_path):
    proc, path, _ = make_session(tmp_path)
    path.write_text(roster_text() + START, encoding="utf-8")
    (proc.executable.parent / "Logs").rename(tmp_path / "Logs")
    assert LoadingRosterReader().poll(proc) is not None


def test_truncation_drops_old_members_and_requires_fresh_loading_state(tmp_path):
    proc, path, _ = make_session(tmp_path)
    path.write_text(roster_text() + START, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is not None
    path.write_text(roster_text(1), encoding="utf-8")
    assert reader.poll(proc) is None and len(reader.parser.members) == 1
    assert reader.local_champion() is None
    path.write_text(roster_text() + START, encoding="utf-8")
    assert reader.poll(proc) is not None


def test_large_log_catches_up_before_publishing_and_bounds_memory(tmp_path, monkeypatch):
    proc, path, _ = make_session(tmp_path)
    monkeypatch.setattr(loading, "MAX_READ", 256)
    path.write_text(roster_text() + START + "x" * (loading.MAX_LINE * 2) + "\n" + EXIT, encoding="utf-8")
    reader = LoadingRosterReader()
    for _ in range(200):
        assert reader.poll(proc) is None  # An ended game must never flash its historical loading roster.
        assert len(reader.pending) <= loading.MAX_LINE
        if not reader._caught_up:
            assert reader.local_champion() is None
        if reader.parser.finished:
            break
    assert reader.parser.finished


@pytest.mark.parametrize("queue,expected", [(2400, True), (450, False), (420, False), (400, False)])
def test_exact_game_id_queue_confirmation(tmp_path, queue, expected):
    proc, path, _ = make_session(tmp_path, queue=queue)
    path.write_text(roster_text() + START, encoding="utf-8")
    assert (LoadingRosterReader().poll(proc) is not None) is expected


@pytest.mark.parametrize("custom,expected", [(True, True), (False, False), (None, False), (1, False), ("true", False)])
def test_locally_verified_custom_queue_requires_explicit_custom_flag(tmp_path, custom, expected):
    proc, path, _ = make_session(tmp_path, queue=3270, custom=custom)
    single = roster_text(1).replace("'redacted-player' -", "'redacted-player' **LOCAL** -")
    path.write_text(single + START, encoding="utf-8")
    reader = LoadingRosterReader()
    got = reader.poll(proc)
    assert (got is not None) is expected
    assert reader.local_champion() == ("Olaf" if expected else None)
    if expected:
        assert len(got.members) == 1 and not got.complete


def test_conflicting_mode_records_revoke_previous_confirmation(tmp_path):
    proc, path, client = make_session(tmp_path)
    path.write_text(roster_text() + START, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is not None and reader.local_champion() == "Gragas"
    with client.open("a", encoding="utf-8") as stream:
        stream.write('000091.000| OKAY| rcp-be-lol-champ-select| /lol-champ-select/v1/session: '
                     + json.dumps({"gameId": int(GAME_ID), "queueId": 450}) + '\n')
    reader._next_mode_check = 0
    assert reader.poll(proc) is None and reader.local_champion() is None
    # Losing the contradictory record later must not resurrect this game's confirmation.
    client.write_text("unrelated\n" * 100, encoding="utf-8")
    reader._next_mode_check = 0
    assert reader.poll(proc) is None and reader.local_champion() is None


def test_mode_record_falling_out_of_tail_does_not_revoke_confirmed_game(tmp_path):
    proc, path, client = make_session(tmp_path)
    path.write_text(roster_text() + START, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is not None and reader.local_champion() == "Gragas"
    with client.open("ab") as stream:
        stream.write(b"unrelated\n" * (loading.CLIENT_TAIL_BYTES // 10 + 2))
    reader._next_mode_check = 0
    assert reader.poll(proc) is not None and reader.local_champion() == "Gragas"
    # A later contradictory record still revokes, even when the old record is no longer in the tail.
    with client.open("a", encoding="utf-8") as stream:
        stream.write('000091.000| OKAY| rcp-be-lol-champ-select| /lol-champ-select/v1/session: '
                     + json.dumps({"gameId": int(GAME_ID), "queueId": 450}) + '\n')
    reader._next_mode_check = 0
    assert reader.poll(proc) is None and reader.local_champion() is None


def test_local_champion_survives_loading_exit_but_never_revives_overlay(tmp_path):
    proc, path, _ = make_session(tmp_path)
    path.write_text(roster_text() + START + EXIT, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None
    assert reader.local_champion() == "Gragas"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(START + LOCAL_LOADED)
    assert reader.poll(proc) is None
    assert reader.local_champion() == "Gragas"
    reader.poll(None)
    assert reader.local_champion() is None


def test_mode_can_finish_confirming_after_loading_exits(tmp_path):
    proc, path, client = make_session(tmp_path)
    original = client.read_bytes()
    client.write_bytes(original[:-1])
    path.write_text(roster_text() + START + EXIT, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None and reader.local_champion() is None
    client.write_bytes(original)
    reader._next_mode_check = 0
    assert reader.poll(proc) is None and reader.local_champion() == "Gragas"


def test_multiple_local_members_and_slot_or_game_id_conflicts_are_rejected(tmp_path):
    proc, path, _ = make_session(tmp_path)
    path.write_text(roster_text() + START, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is not None and reader.local_champion() == "Gragas"
    extra_local = roster_text(1).splitlines()[1].replace("'redacted-player' -", "'redacted-player' **LOCAL** -")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(extra_local + '\n')
    reader.poll(proc)
    assert reader.local_champion() is None
    for conflicting in (roster_text().splitlines()[5].replace("Champion(Gragas)", "Champion(Teemo)"),
                        '000004.000| ALWAYS| GameStartData::GameID=999999999'):
        parser = parse(roster_text() + START)
        parser.feed(conflicting)
        assert parser.snapshot("g") is None and parser.local_champion() is None


def test_identical_local_duplicates_are_allowed_but_replaced_log_drops_local(tmp_path):
    proc, path, _ = make_session(tmp_path)
    path.write_text(roster_text() + START + roster_text() + EXIT, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None and reader.local_champion() == "Gragas"
    replacement = path.with_suffix('.new')
    replacement.write_text(roster_text(1) + START, encoding="utf-8")
    replacement.replace(path)
    assert len(reader.poll(proc).members) == 1
    assert reader.local_champion() is None


def test_wrong_game_id_old_record_or_untrusted_record_cannot_confirm_mode(tmp_path):
    proc, path, client = make_session(tmp_path, game_id="987654321")
    path.write_text(roster_text() + START, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None
    client.write_text('000090.000| OKAY| chat| ' + json.dumps({"gameId": int(GAME_ID), "queueId": 2400}) + '\n')
    assert loading.queue_for_game(proc, GAME_ID) is None
    # Correct endpoint but implausible stale timestamp from a long-ago game is rejected.
    text = '000000.000| OKAY| rcp-be-lol-champ-select| /lol-champ-select/v1/session: '
    client.write_text(text + json.dumps({"gameId": int(GAME_ID), "queueId": 2400}) + '\n')
    old_proc = GameProcess(proc.pid, proc.executable, proc.created_at + 1000, proc.creation_ticks)
    assert loading.queue_for_game(old_proc, GAME_ID) is None


def test_unknown_mode_can_be_confirmed_when_client_log_flushes(tmp_path):
    proc, path, client = make_session(tmp_path)
    body = client.read_bytes()
    client.write_bytes(body[:-1])
    path.write_text(roster_text() + START, encoding="utf-8")
    reader = LoadingRosterReader()
    assert reader.poll(proc) is None
    client.write_bytes(body)
    reader._next_mode_check = 0
    assert reader.poll(proc) is not None


def test_game_id_is_not_taken_from_command_line_or_player_text():
    p = parse('000000.000| ALWAYS| CFG| Command Line: "-GameID=123456789"\n')
    assert p.game_id is None
    p.feed('000000.547| ALWAYS| GameStartData::GameID=123456789')
    assert p.game_id == GAME_ID


@pytest.mark.parametrize("local,label", [(True, ["我方", "敌方"]), (False, ["蓝方", "红方"])])
def test_engine_builds_statistics_without_changing_personal_hero(local, label):
    roster = parse(roster_text(local=local) + START).snapshot("g")
    heroes = {str(i): {"alias": alias} for i, alias in enumerate(CHAMPIONS)}
    fake = SimpleNamespace(store=SimpleNamespace(heroes=heroes), state={"hero_id": "17"},
                           hero_brief=lambda hid: {"hero_id": hid, "win": .5})
    data = Engine._loading_roster_info(fake, roster)
    assert [team["label"] for team in data["teams"]] == label
    assert data["complete"] and len(data["teams"][0]["members"]) == 5
    assert fake.state["hero_id"] == "17"
    heroes.pop("9")
    assert Engine._loading_roster_info(fake, roster)["complete"] is False


def test_loading_thread_is_independent_and_clears_state_on_stop(monkeypatch, tmp_path, store):
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine.state["hero_id"] = "17"
    proc = GameProcess(9, Path("Game/League of Legends.exe"), 1, 10000000)
    roster = LoadingRoster(proc.key, (LoadingMember(100, 0, "Teemo", True),), False)
    monkeypatch.setattr(loading, "LoadingRosterReader", lambda: SimpleNamespace(
        poll=lambda p: roster, local_champion=lambda: None, reset=lambda: None))
    monkeypatch.setattr(window, "find_game_window", lambda: 3)
    monkeypatch.setattr(window, "game_process", lambda hwnd: proc)
    monkeypatch.setattr(window, "lower_thread_priority", lambda: None)
    started = time.monotonic()
    thread = threading.Thread(target=engine._loading_loop, daemon=True)
    thread.start()
    try:
        while engine.state.get("loading_roster") is None and time.monotonic() - started < 2:
            time.sleep(.01)
        assert engine.state["loading_roster"]["teams"][0]["members"][0]["hero_id"] == "17"
        assert engine.state["hero_id"] == "17"
        assert "loading_roster" in engine.snapshot()
    finally:
        engine.stop()
        thread.join(2)
    assert not thread.is_alive() and engine.state["loading_roster"] is None
