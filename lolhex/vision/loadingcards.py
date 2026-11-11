"""Locate loading-card statistics using current-game names, never team-side guesses.

Only the ten small hero-title regions are read. Images/text are neither persisted nor
logged. The measured five-column 16:9 layout is scaled into the game client rectangle;
centred or otherwise rearranged short custom-game rows still need separate calibration.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .matcher import normalize


def _box(width, height, cx, cy, bw, bh):
    scale = min(width / 1920, height / 1080)
    ox, oy = (width - 1920 * scale) / 2, (height - 1080 * scale) / 2
    return (max(0, round(ox + (cx - bw / 2) * scale)),
            max(0, round(oy + (cy - bh / 2) * scale)),
            min(width, round(ox + (cx + bw / 2) * scale)),
            min(height, round(oy + (cy + bh / 2) * scale)))


def name_boxes(width, height):
    """Ten (x0,y0,x1,y1) title crops, row-major, relative to the captured frame."""
    if width <= 0 or height <= 0:
        return []
    return [_box(width, height, 365 + 296 * col, 420 + 523 * row, 255, 36)
            for row in range(2) for col in range(5)]


def badge_box(width, height, row, col):
    """A badge directly above the title, about three quarters of the card width."""
    return _box(width, height, 365 + 296 * col, 386 + 523 * row, 191, 30)


def _members(roster):
    for team in (roster or {}).get("teams") or []:
        for member in team.get("members") or []:
            if member.get("hero_id"):
                yield team.get("side"), member


def _signature(roster):
    return (str((roster or {}).get("game_key") or ""), tuple(sorted(
        (str(side), str(member.get("slot")), str(member["hero_id"]),
         str(member.get("name") or ""), str(member.get("nick") or ""))
        for side, member in _members(roster))))


def match_name(text, score, roster):
    """Strict normalized containment among current roster names; ambiguity is a miss."""
    if type(score) not in (int, float) or not math.isfinite(score) or not .85 <= score <= 1:
        return None
    query = normalize(text)
    if not query:
        return None
    found = set()
    for _, member in _members(roster):
        for field in ("name", "nick"):
            term = normalize(member.get(field) or "")
            if term and (query == term or (len(term) >= 2 and term in query)):
                found.add(str(member["hero_id"]))
    return next(iter(found)) if len(found) == 1 else None


class LoadingCardLocator:
    """Incremental round-robin OCR; at most one title recognition per step."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._key = None
        self._fingerprints = [None] * 10
        self._stable = [0] * 10
        self._visited = set()
        self._direct = {}
        self._cursor = 0

    @staticmethod
    def _image_signature(crop):
        # Same comparison policy as champselect.Memo: preserve narrow text strokes,
        # tolerate minor screenshot/skin-background noise, avoid average-difference
        # tests which can mistake a one-character name change for an unchanged row.
        height, width = crop.shape[:2]
        cols = max(1, min(24 * 8, round(24 * width / max(1, height))))
        return cv2.resize(crop, (cols, 24), interpolation=cv2.INTER_AREA).astype(np.float32)

    def step(self, img, roster, recognize):
        if (img is None or getattr(img, "size", 0) == 0 or not roster
                or not roster.get("game_key")):
            self.reset()
            return []
        height, width = img.shape[:2]
        key = (width, height, _signature(roster))
        if key != self._key:
            self.reset()
            self._key = key
        boxes = name_boxes(width, height)
        if any(x1 <= x0 or y1 <= y0 for x0, y0, x1, y1 in boxes):
            self.reset()
            return []
        crops = []
        for index, (x0, y0, x1, y1) in enumerate(boxes):
            crop = img[y0:y1, x0:x1]
            crops.append(crop)
            fingerprint = self._image_signature(crop)
            previous = self._fingerprints[index]
            same = previous is not None and float(np.percentile(np.abs(previous - fingerprint), 99)) < 12
            if not same:
                self._direct.pop(index, None)
                self._visited.discard(index)
                self._stable[index] = 1
                self._fingerprints[index] = fingerprint
            else:
                self._stable[index] = min(2, self._stable[index] + 1)
        # Round-robin prevents one unreadable skin name from blocking the other cards.
        # Unchanged directly confirmed titles need no repeated OCR.
        for offset in range(10):
            index = (self._cursor + offset) % 10
            if index in self._direct:
                continue
            self._cursor = (index + 1) % 10
            text, score = recognize(crops[index])
            hero_id = match_name(text, score, roster)
            self._visited.add(index)
            if hero_id:
                self._direct[index] = hero_id
            break
        return self._cards(width, height, roster)

    @staticmethod
    def _full_teams(roster):
        teams = []
        seen_sides = set()
        for team in roster.get("teams") or []:
            side = team.get("side")
            if side not in (100, 200) or side in seen_sides:
                return []
            seen_sides.add(side)
            slots = {}
            for member in team.get("members") or []:
                slot = member.get("slot")
                if (type(slot) is not int or not 0 <= slot < 5 or slot in slots
                        or not member.get("hero_id")):
                    return []
                slots[slot] = str(member["hero_id"])
            teams.append((side, slots))
        return teams if len(teams) == 2 else []

    def _cards(self, width, height, roster):
        result = dict(self._direct)
        teams = self._full_teams(roster)
        if teams:
            row_anchors = [{i % 5: hid for i, hid in self._direct.items() if i // 5 == row}
                           for row in range(2)]
            for row, anchors in enumerate(row_anchors):
                indices = range(row * 5, row * 5 + 5)
                # Do not infer unseen titles after just two early matches: a later
                # card can contradict the presumed slot order. After animations,
                # a failed special-skin read is usable evidence once the row settles;
                # unknown text must not permanently disable row completion.
                if (len(set(anchors.values())) < 2 or not all(i in self._visited for i in indices)
                        or not all(self._stable[i] >= 2 for i in indices)):
                    continue
                candidates = [(side, slots) for side, slots in teams if len(slots) == 5
                              and all(slots.get(col) == hid for col, hid in anchors.items())]
                if len(candidates) != 1:
                    continue
                side, slots = candidates[0]
                other = next(table for other_side, table in teams if other_side != side)
                # Even one contradictory direct match in the opposite row blocks a
                # row-wide assumption. Directly recognized cards remain usable.
                if any(other.get(col) != hid for col, hid in row_anchors[1 - row].items()):
                    continue
                for col, hid in slots.items():
                    result[row * 5 + col] = hid
        return [{"hero_id": hid, "box": list(badge_box(width, height, *divmod(index, 5)))}
                for index, hid in sorted(result.items())]
