"""真实 Qt 绘制：读秒两张/三张卡可见，金框受完整性控制，窗口不抢焦点。"""
import logging
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from lolhex.ui import overlay
from lolhex.vision import window


def offered(centers, *, complete):
    cards = []
    for i, center in enumerate(centers):
        cards.append({"hero_id": str(i + 1), "grade": "", "rank": 20 + i, "win": .482 + i * .011,
                      "box": (719 + center - 172, 375 + 337, 719 + center + 172, 375 + 897)})
    return {"bench": [], "team": [], "cards_complete": complete, "cards": cards}


@pytest.mark.parametrize("centers,complete", [([920, 1460], False), ([920, 1460], True),
                                              ([755, 1200, 1645], True)])
def test_card_percentages_render_inside_candidates_at_150pct(monkeypatch, caplog, centers, complete):
    app = QApplication.instance() or QApplication([])
    layer = overlay.ChampSelectLayer()
    layer.resize(1600, 900)
    layer.origin = QPoint(719, 375)
    monkeypatch.setattr(layer, "_scale", lambda: 1.5)
    layer.cs = offered(centers, complete=complete)
    published = []
    monkeypatch.setattr(layer, "_publish", lambda rects, scale: published.append(rects.copy()))
    image = QImage(2400, 1350, QImage.Format_ARGB32)
    image.setDevicePixelRatio(1.5)
    image.fill(0)
    try:
        with caplog.at_level(logging.INFO, logger="lolhex.ui.overlay"):
            layer.render(image)
            layer.render(image)
        assert sum("读秒候选绘制" in rec.message for rec in caplog.records) == 1
        assert f"界内胜率={len(centers)}/{len(centers)}" in caplog.text
        argb = np.frombuffer(image.constBits(), dtype=np.uint32).reshape(1350, 2400)
        for i, cx in enumerate(centers):
            # 在卡面上半部实际绘出了该卡胜率字色；背景/排名文字不足以让断言通过。
            pixels = argb[337:615, cx-165:cx+165]
            color = overlay.win_color(layer.cs["cards"][i]["win"]).rgb()
            assert np.count_nonzero(pixels == color) > 8
        gold = np.count_nonzero(argb == 0xfff0c85a)
        assert (gold > 50) == complete
        assert published
        # 所登记的整块不透明标签均在屏幕内且不遮挡卡名 OCR 横条（物理 y=750 起）。
        labels = [r for r in published[-1] if r.width() > 10 and r.height() > 10]
        assert len(labels) == len(centers)
        assert all(layer.rect().contains(r) and 337 < r.y()*1.5 < r.bottom()*1.5 < 750
                   for r in labels)
        layer.cs = None
        layer.paintEvent(None)  # 疑似界面退出或重入时也能清空标注
    finally:
        layer.close()


def test_zorder_once_per_foreground_card_phase(monkeypatch):
    app = QApplication.instance() or QApplication([])
    layer = overlay.ChampSelectLayer()
    monkeypatch.setattr(layer, "_place", lambda *_: None)
    calls = []
    monkeypatch.setattr(window, "topmost_without_focus", lambda own, target: calls.append(target) or True)
    empty = offered([], complete=False)
    pair = offered([920, 1460], complete=False)
    rect = window.Rect(719, 375, 2400, 1350)
    try:
        for cs in (empty, pair, empty, pair):
            layer.update_from({"champ_select": cs}, rect, 11)
        assert calls == [11]
        layer.update_from({"champ_select": pair}, None, 11)  # 后台不提升
        assert calls == [11]
        layer.update_from({"champ_select": pair}, rect, 11)
        layer.update_from({"champ_select": {"bench": [{"hero_id":"4"}]}}, rect, 11)
        layer.update_from({"champ_select": pair}, rect, 11)
        assert calls == [11, 11, 11]
    finally:
        layer.close()


def test_native_topmost_preserves_focus(monkeypatch):
    seen = []
    native = SimpleNamespace(SetWindowPos=lambda *args: seen.append(args) or 1)
    monkeypatch.setattr(window.ctypes, "windll", SimpleNamespace(user32=native), raising=False)
    monkeypatch.setattr(window, "is_foreground", lambda hwnd: hwnd == 41)
    assert not window.topmost_without_focus(1, 12)
    assert not seen
    assert window.topmost_without_focus(1, 41)
    _, target, _, _, _, _, flags = seen[0]
    assert target.value == window.wintypes.HWND(-1).value
    assert flags & 0x10  # SWP_NOACTIVATE
