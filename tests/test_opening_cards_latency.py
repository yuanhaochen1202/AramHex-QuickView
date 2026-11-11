"""首抽淡入时先回传已知卡片，二选一和慢兜底仍可完成。"""
from types import SimpleNamespace
import logging

import cv2
import numpy as np
import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision import champselect, window
from lolhex.vision.ocr import TextLine


def frame(*inks, title=True):
    img = np.zeros((1080, 1920, 3), np.uint8)
    if title:
        img[175:191, 890:1030] = 250
    for i, ink in enumerate(inks):
        x = 600 + 310 * i
        img[628:651, x:x + 90] = ink
    return img


def faint_names():
    # 暗蓝卡片底上出现细而暗的称号，还没达到快读使用的亮度阈值。
    img = frame()
    img[600:690, 430:1490] = 40
    for x in (600, 910):
        cv2.putText(img, "Hero", (x, 650), cv2.FONT_HERSHEY_SIMPLEX,
                    .95, (68, 68, 68), 2, cv2.LINE_AA)
    return img


@pytest.fixture
def reader(tmp_path, store, monkeypatch):
    engine = Engine(Settings(), home=tmp_path, store=store)
    names = {180: "沙漠皇帝", 200: "黑暗之女", 220: "卡牌大师", 250: "选择你的英雄"}
    ids = {"沙漠皇帝": "268", "黑暗之女": "1", "卡牌大师": "4"}
    detect_calls = []
    full = []

    def detect(band):
        detect_calls.append(band.shape[:2])
        return full

    monkeypatch.setattr(engine, "_hero_by_name", ids.get)
    monkeypatch.setattr(engine, "hero_brief", lambda hid: {"hero_id": hid, "win": 0.5})
    monkeypatch.setattr("lolhex.vision.ocr.OCR.get", lambda: SimpleNamespace(
        recognize=lambda crop: (names.get(int(crop.max()), ""), 0.98), detect=detect))
    clock = [100.0]
    monkeypatch.setattr(champselect.time, "monotonic", lambda: clock[0])
    return engine, clock, detect_calls, full


def test_fade_publishes_partial_then_three_without_blocking(reader):
    engine, clock, detect, _ = reader
    assert engine.analyze_champ_select(frame())["cards"] == []
    assert engine._cs_memo.opening.fast_retry()
    clock[0] += .2
    first = engine.analyze_champ_select(frame(180, 175, 175), (123, 120))
    assert [c["hero_id"] for c in first["cards"]] == ["268"]
    assert not first["cards_complete"]
    clock[0] += .2
    second = engine.analyze_champ_select(frame(180, 200, 175), (123, 120))
    assert [c["hero_id"] for c in second["cards"]] == ["268", "1"]
    assert not second["cards_complete"]
    clock[0] += .2
    final = engine.analyze_champ_select(frame(180, 200, 220), (123, 120))
    assert [c["hero_id"] for c in final["cards"]] == ["268", "1", "4"]
    assert final["cards"][0]["box"] == first["cards"][0]["box"]
    assert final["cards_complete"] and not engine._cs_memo.opening.fast_retry()
    assert detect == []


def test_two_card_offer_and_new_round(reader):
    engine, clock, detect, _ = reader
    result = engine.analyze_champ_select(frame(180, 200))
    assert len(result["cards"]) == 2 and not result["cards_complete"]
    clock[0] += 1.5
    assert engine.analyze_champ_select(frame(180, 200))["cards_complete"]
    assert not engine.analyze_champ_select(frame(title=False))
    clock[0] += 8
    other = engine.analyze_champ_select(frame(220, 175))
    assert [c["hero_id"] for c in other["cards"]] == ["4"]
    assert not other["cards_complete"] and engine._cs_memo.opening.fast_retry()
    assert not detect


def test_temporarily_dark_third_slot_never_turns_known_triple_into_pair(reader):
    engine, clock, _, _ = reader
    assert len(engine.analyze_champ_select(frame(180, 200, 175))["cards"]) == 2
    for second in (100.5, 102.0, 103.0):
        clock[0] = second
        partial = engine.analyze_champ_select(frame(180, 200))
        assert len(partial["cards"]) == 2 and not partial["cards_complete"]


def test_late_readable_name_and_bounded_full_scan(reader):
    engine, clock, detect, full = reader
    engine.analyze_champ_select(frame())
    clock[0] += 1.6
    assert engine.analyze_champ_select(frame())["cards"] == []  # 纯空区域不整条检测
    assert not detect
    clock[0] += .15
    partial = engine.analyze_champ_select(frame(180, 175))
    assert [c["hero_id"] for c in partial["cards"]] == ["268"]
    assert not detect  # 即使淡入较晚，这一轮刚看清的卡也先发
    clock[0] += .15
    full.extend([TextLine("沙漠皇帝", .9, (170, 25, 260, 60)),
                 TextLine("黑暗之女", .9, (480, 25, 570, 60))])
    rescued = engine.analyze_champ_select(frame(180, 175))
    assert [c["hero_id"] for c in rescued["cards"]] == ["268", "1"]
    assert detect == [(90, 1060)]


def test_fallback_finishing_after_card_screen_closes_is_discarded(reader, monkeypatch):
    engine, clock, _, full = reader
    rect = window.Rect(0, 0, 1920, 1080)
    monkeypatch.setattr(window, "client_rect", lambda hwnd: rect)
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: True)
    monkeypatch.setattr(window, "find_client_window", lambda: 7)
    monkeypatch.setattr(window, "find_game_window", lambda: 0)
    screens = iter([faint_names(), faint_names(), frame(200, 220, 175, title=False)])
    monkeypatch.setattr("lolhex.vision.capture.grab", lambda r: next(screens))
    engine._champ_select_scan(7)
    full.extend([TextLine("沙漠皇帝", .9, (170, 25, 260, 60)),
                 TextLine("黑暗之女", .9, (480, 25, 570, 60))])
    clock[0] += 2
    engine._champ_select_scan(7)
    assert not engine.state["champ_select"]


def test_gradient_and_card_borders_do_not_trigger_empty_band_ocr(reader):
    engine, clock, detect, _ = reader
    img = frame()
    band = img[600:690, 430:1490]
    band[:] = np.linspace(24, 130, band.shape[1], dtype=np.uint8)[None, :, None]
    band[:, 120:123] = 210
    band[:, 450:454] = 240
    band[:, 810:813] = 240
    band[5:8] = 200
    assert not champselect.caption_detail(band, weak=True)
    for elapsed in (0, 1.5, 3.0, 4.6, 8.0):
        clock[0] = 100.0 + elapsed
        snap = engine.analyze_champ_select(img)
        assert not snap["cards"]
        if elapsed == 3.0:
            assert engine._cs_memo.opening.fast_retry()
    assert not detect


def test_low_contrast_titles_can_still_use_full_ocr(reader):
    engine, clock, detect, full = reader
    img = faint_names()
    band = img[600:690, 430:1490]
    assert not champselect.text_spans(band)
    assert champselect.caption_detail(band)
    assert not engine.analyze_champ_select(img)["cards"]
    full.extend([TextLine("沙漠皇帝", .9, (170, 25, 260, 60)), TextLine("黑暗之女", .9, (480, 25, 570, 60))])
    clock[0] += 1.5
    assert [row["hero_id"] for row in engine.analyze_champ_select(img)["cards"]] == ["268", "1"]
    assert len(detect) == 1


def test_sparse_low_contrast_name_has_bounded_escape(reader):
    engine, clock, detect, full = reader
    img = frame()
    band = img[600:690, 430:1490]
    band[:] = 40
    for x in (174, 185, 482, 494):
        band[32:57, x:x+6] = 54
    assert not champselect.caption_detail(band)
    assert champselect.caption_detail(band, weak=True)
    engine.analyze_champ_select(img)
    clock[0] += 1.6
    engine.analyze_champ_select(img)
    assert not detect
    clock[0] += 3.2
    full.extend([TextLine("沙漠皇帝", .9, (170, 25, 260, 60)), TextLine("卡牌大师", .9, (480, 25, 570, 60))])
    assert [c["hero_id"] for c in engine.analyze_champ_select(img)["cards"]] == ["268", "4"]
    assert len(detect) == 1


def test_missed_title_drops_names_keeps_clock_only_for_short_gap(reader):
    engine, clock, _, _ = reader
    engine.analyze_champ_select(frame(180, 200))
    opening = engine._cs_memo.opening
    clock[0] += .3
    assert not engine.analyze_champ_select(frame(title=False))
    assert opening.last_ids == () and not opening.complete
    clock[0] += .2
    next_frame = engine.analyze_champ_select(frame(220))
    assert [c["hero_id"] for c in next_frame["cards"]] == ["4"]
    assert engine._cs_memo.opening.started == 100.0
    engine.analyze_champ_select(frame(title=False))
    clock[0] += 8
    engine.analyze_champ_select(frame(180))
    assert engine._cs_memo.opening.started == clock[0]


def test_stale_fallback_rereads_new_frame_and_times_publication(reader, monkeypatch, caplog):
    engine, clock, detect, full = reader
    rect = window.Rect(0, 0, 1920, 1080)
    monkeypatch.setattr(window, "client_rect", lambda hwnd: rect)
    monkeypatch.setattr(window, "find_client_window", lambda: 7)
    monkeypatch.setattr(window, "find_game_window", lambda: 0)
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: True)
    screens = iter([faint_names(), faint_names(), frame(200, 220)])
    def grab(_):
        pic = next(screens)
        if detect:
            clock[0] += 2.4
        return pic
    monkeypatch.setattr("lolhex.vision.capture.grab", grab)
    engine._champ_select_scan(7)
    full.append(TextLine("沙漠皇帝", .99, (170, 25, 260, 60)))
    clock[0] += 2.0
    with caplog.at_level(logging.INFO, logger="lolhex.vision.champselect"):
        engine._champ_select_scan(7)
    assert [c["hero_id"] for c in engine.state["champ_select"]["cards"]] == ["1", "4"]
    assert "初见标题后 4400 毫秒发布首批 2 张，有胜率 2 张" in caplog.text
