"""A slow relocation must not delay live titles or resurrect an older offer."""
import threading
import time

import numpy as np
import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision import capture, recovery, window
from lolhex.vision.detector import AugmentDetector, Calibration, Offer
from lolhex.vision.matcher import NameMatcher
from lolhex.vision.window import Rect


BOXES = [(5, 20, 25, 30), (35, 20, 55, 30), (65, 20, 85, 30)]
RECT = Rect(0, 0, 100, 60)
NAMES = {str(i): chr(0x4e00 + i) * 2 for i in range(1, 5)}


def game_window(monkeypatch):
    monkeypatch.setattr(window, "find_game_window", lambda: 7)
    monkeypatch.setattr(window, "client_rect", lambda hwnd: RECT)
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: hwnd == 7)
    monkeypatch.setattr(window, "lower_thread_priority", lambda: None)


def offer(ids=(1, 2, 3)):
    return Offer([str(i) for i in ids], [NAMES[str(i)] for i in ids], [100] * 3,
                 list(BOXES), padded=True)


def scene(ids=(1, 2, 3)):
    img = np.zeros((RECT.height, RECT.width, 3), np.uint8)
    for val, (x0, y0, x1, y1) in zip(ids, BOXES):
        img[y0:y1, x0:x1] = val
    return img


class GlyphReader:
    def recognize(self, tile):
        return NAMES.get(str(int(tile.mean())), ""), 1.0


def test_first_offer_is_published_while_full_ocr_is_still_busy(monkeypatch, tmp_path, store):
    game_window(monkeypatch)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame", hero_id="17")
    matcher = NameMatcher(NAMES)
    title_ocr, full_ocr = GlyphReader(), object()
    detector = AugmentDetector(matcher, ocr=title_ocr)
    engine._detector = detector
    engine._sync_augment_tables = lambda det: None
    engine._save_offer_shot = lambda rect: None
    detector.calibration = Calibration.default(RECT.width, RECT.height)
    detector.calibration.guess = False
    entered, release, live, published, completed = [threading.Event() for _ in range(5)]
    monkeypatch.setattr(recovery, "OCR", lambda: full_ocr)
    monkeypatch.setattr(capture, "grab", lambda r: np.zeros((r.height, r.width, 3), np.uint8))

    def slow_lookup(private, frame, origin):
        assert private is not detector and private.ocr is full_ocr
        entered.set()
        release.wait(5)
        completed.set()
        return offer()

    monkeypatch.setattr(AugmentDetector, "scan_full", slow_lookup)
    monkeypatch.setattr(detector, "check_strip", lambda frame, rect: offer((4, 3, 2)) if live.is_set() else None)
    keys = []

    def publish(current):
        if current is not None:
            keys.append(current.key())
            published.set()

    engine._on_offer = publish
    thread = threading.Thread(target=engine._vision_loop, daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        live.set()
        assert published.wait(1.5), "fast titles were held behind an unfinished full scan"
        assert not completed.is_set()
        release.set()
        assert completed.wait(1)
        live.clear()
        time.sleep(.65)
        assert keys and all(k == ("4", "3", "2") for k in keys)
    finally:
        release.set()
        engine.stop()
        thread.join(2)
    assert not thread.is_alive()


@pytest.mark.parametrize("live_ids,expected", [
    ((0, 0, 0), None), ((1, 4, 4), None), ((1, 2, 4), ("1", "2", None)),
    ((1, 2, 3), ("1", "2", "3")),
])
def test_relocation_only_publishes_reverified_columns(monkeypatch, tmp_path, store, live_ids, expected):
    game_window(monkeypatch)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame")
    matcher = NameMatcher(NAMES)
    detector = AugmentDetector(matcher, tmp_path / "calibration.json", ocr=GlyphReader())
    before, current = scene(), scene(live_ids)
    tiles = [before[b:d, a:c].copy() for a, b, c, d in BOXES]
    normalized = [(a/100, b/60, c/100, d/60) for a, b, c, d in BOXES]
    calibration = Calibration(normalized, (100, 60))
    hint = recovery.Candidate((0, 7, RECT), matcher, offer(), calibration, tiles)
    monkeypatch.setattr(capture, "grab", lambda r: current[r.top:r.top+r.height, r.left:r.left+r.width])
    result = engine._accept_recovery(detector, hint)
    assert (result.key() if result else None) == expected
    assert detector.calib_path.exists() == (expected == ("1", "2", "3"))


def test_session_changed_during_live_verification_discards_calibration(monkeypatch, tmp_path, store):
    game_window(monkeypatch)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame")
    detector = AugmentDetector(NameMatcher(NAMES), tmp_path / "calibration.json", ocr=GlyphReader())
    orig = scene()
    tiles = [orig[b:d, a:c].copy() for a, b, c, d in BOXES]
    candidate = recovery.Candidate((0, 7, RECT), detector.matcher, offer(), Calibration.default(100, 60), tiles)
    def finish_game_during_capture(r):
        engine._on_game_end()
        return orig[r.top:r.top+r.height, r.left:r.left+r.width]
    monkeypatch.setattr(capture, "grab", finish_game_during_capture)
    assert engine._accept_recovery(detector, candidate) is None
    assert not detector.calib_path.exists()


def test_second_model_failure_preserves_synchronous_full_fallback(monkeypatch, tmp_path, store):
    game_window(monkeypatch)
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame")
    detector = AugmentDetector(NameMatcher(NAMES), ocr=GlyphReader())
    worker = recovery.FullScanRecovery()
    def unavailable():
        raise MemoryError("secondary OCR unavailable")
    monkeypatch.setattr(recovery, "OCR", unavailable)
    monkeypatch.setattr(capture, "grab", lambda rect: scene())
    monkeypatch.setattr(detector, "scan_full", lambda img, origin: offer())
    assert engine._observed_scan(detector, RECT, full=True, reason="fallback",
                                 hwnd=7, background=worker) is None
    worker._future.result(timeout=2)
    assert worker.disabled
    result = engine._observed_scan(detector, RECT, full=True, reason="fallback",
                                   hwnd=7, background=worker)
    assert result.key() == ("1", "2", "3")
