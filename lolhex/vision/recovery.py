"""Single-flight, low-priority relocation of unreadable augment titles."""
from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, replace

import numpy as np

from . import capture, window
from .detector import AugmentDetector, Calibration, Offer, REC_HEIGHT, _resize
from .ocr import OCR
from .window import Rect

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    context: tuple
    matcher: object
    offer: Offer
    calibration: Calibration | None
    tiles: list

    def verify(self, detector):
        """Re-capture the titles together. Keep only identities still on screen."""
        boxes = [b for b in self.offer.boxes if b is not None]
        x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
        area = Rect(x, y, max(b[2] for b in boxes) - x, max(b[3] for b in boxes) - y)
        band = capture.grab(area)
        slots, texts, scores = list(self.offer.slots), list(self.offer.texts), list(self.offer.scores)
        for i, box in enumerate(self.offer.boxes):
            if slots[i] is None or box is None:
                continue
            a, b, c, d = box
            tile = band[b-y:d-y, a-x:c-x]
            if tile.size and np.array_equal(tile, self.tiles[i]):
                continue
            text, confidence = (detector.ocr.recognize(_resize(tile, REC_HEIGHT / tile.shape[0]))
                                if tile.size else ("", 0.0))
            matched, score = detector.matcher.match(text)
            # A shifted/rerolled column must never carry its previous score.
            if matched == slots[i] and confidence >= .75 and score >= 90:
                texts[i], scores[i] = text, score
            else:
                slots[i], texts[i], scores[i] = None, "", 0.0
        if len({aid for aid in slots if aid}) < 2:
            return None
        return replace(self.offer, slots=slots, texts=texts, scores=scores, at=time.time(), source="recovery")


class FullScanRecovery:
    """One background job, no queue. Only the vision thread can consume its result."""

    def __init__(self):
        self._future = None
        self._cancel = None
        self._ocr = None
        self.disabled = False

    def start(self, detector, context, sequence):
        if self._future is not None:
            return
        future, cancel = Future(), threading.Event()
        self._future, self._cancel = future, cancel
        # Snapshot isolated state. This detector cannot write the live calibration,
        # share its tracking cache, or hold the title recognizer's model lock.
        matcher, calibration = detector.matcher, copy.deepcopy(detector.calibration)
        rarities, color_check = detector.rarities, detector.color_check

        def run():
            try:
                window.lower_thread_priority()
                if cancel.is_set():
                    future.set_result(None)
                    return
                if self._ocr is None:
                    try:
                        self._ocr = OCR()
                    except Exception:
                        self.disabled = True  # use the normal full path if a second model cannot load
                        raise
                _, hwnd, rect = context
                if (cancel.is_set() or not window.is_foreground(hwnd)
                        or window.client_rect(hwnd) != rect):
                    future.set_result(None)
                    return
                helper = AugmentDetector(matcher, ocr=self._ocr, rarities=rarities)
                helper.color_check = color_check
                helper.calibration = calibration
                start = time.perf_counter()
                frame = capture.grab(rect)
                captured = time.perf_counter()
                offer = helper.scan_full(frame, (rect.left, rect.top))
                finished = time.perf_counter()
                log.info("后台整块扫描：scan=%d capture=%.0fms work=%.0fms matches=%d",
                         sequence, (captured-start)*1000, (finished-captured)*1000,
                         offer.matched() if offer else 0)
                if offer is None or cancel.is_set():
                    future.set_result(None)
                    return
                tiles = []
                for box in offer.boxes:
                    if box is None:
                        tiles.append(None)
                        continue
                    a, b, c, d = box
                    if not (rect.left <= a < c <= rect.left + rect.width
                            and rect.top <= b < d <= rect.top + rect.height):
                        future.set_result(None)
                        return
                    tiles.append(frame[b-rect.top:d-rect.top, a-rect.left:c-rect.left].copy())
                offer.trace = dict(scan=sequence, path="background_full", reason="fallback", context=context,
                                   started=start, captured=captured, finished=finished,
                                   capture_ms=(captured-start)*1000, work_ms=(finished-captured)*1000)
                future.set_result(Candidate(context, matcher, offer,
                                            helper.calibration if offer.padded else None, tiles))
            except Exception:
                log.exception("后台整块扫描失败")
                future.set_result(None)
            finally:
                capture.release()

        try:
            threading.Thread(target=run, name="augment-relocate", daemon=True).start()
        except RuntimeError:
            self._future = None
            self.disabled = True
            raise

    def invalidate(self, *, release_model=False):
        if self._cancel is not None:
            self._cancel.set()
        if self._future is not None and self._future.done():
            self._future = None
        if release_model and self._future is None:
            self._ocr = None

    def take(self):
        if self._future is None or not self._future.done():
            return None
        future, self._future = self._future, None
        return None if self._cancel.is_set() else future.result()
