"""Countdown results must be usable even before any hero is selected."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from lolhex.ui import overlay


@pytest.fixture
def setup(monkeypatch):
    app = QApplication.instance() or QApplication([])
    front, clock = [True], [20.0]
    monkeypatch.setattr(overlay.win, "IS_WINDOWS", False)
    monkeypatch.setattr(overlay.win, "is_foreground", lambda hwnd: hwnd == 10 and front[0])
    monkeypatch.setattr(overlay.win, "add_companion", lambda hwnd: None)
    monkeypatch.setattr(overlay.time, "monotonic", lambda: clock[0])
    engine = SimpleNamespace(store=SimpleNamespace(heroes={}), hero_panel=lambda hid: None,
                             hero_brief=lambda hid: {})
    panel = overlay.ControlPanel(engine)
    panel.target_hwnd = 10
    panel.show()
    yield panel, front, clock
    panel.close()
    panel.body_win.close()
    app.processEvents()


def draw(cards=(), complete=False, show_win=True):
    return dict(phase="client", hero_id=None,
                champ_select={"cards": list(cards), "cards_complete": complete},
                settings={"show_winrate": show_win})


LEFT = {"slot": 0, "hero_id": "1", "name": "左侧英雄", "win": .473, "grade": "C"}
RIGHT = {"slot": 1, "hero_id": "2", "name": "右侧英雄", "win": .581, "grade": "S"}


def test_draw_opens_without_selected_hero_and_presents_cards_left_to_right(setup):
    panel, front, _ = setup
    panel.update_from(draw())
    assert panel.expanded and panel.body_win.isVisible()
    assert "正在识别候选英雄" in panel.hero_label.text()
    assert panel.search.isHidden() and panel.confirm_hero.isHidden()
    panel.update_from(draw([RIGHT, LEFT], True))
    text = panel.hero_label.text()
    assert text.index("左侧英雄") < text.index("47.3%") < text.index("右侧英雄") < text.index("58.1%")
    assert "未识别到英雄" not in text and "确认卡位" not in text
    panel.update_from(draw([{**LEFT, "win": None}]))
    assert "暂无胜率" in panel.hero_label.text() and "确认卡位" in panel.hero_label.text()
    panel.update_from(draw([LEFT], show_win=False))
    assert "左侧英雄" in panel.hero_label.text() and "%" not in panel.hero_label.text()
    assert not panel.search.hasFocus()
    small = overlay.win.Rect(0, 0, 1280, 720)
    panel.place(small, in_client=True)
    assert panel.body.width() * panel.devicePixelRatioF() <= small.width * .20
    front[0] = False
    panel.place(overlay.win.Rect(0, 0, 2400, 1350), in_client=True)
    assert not panel.body_win.isVisible()


def test_manual_close_survives_new_read_and_transient_miss_but_rearms_next_draw(setup):
    panel, _, clock = setup
    panel.update_from(draw([LEFT]))
    panel.toggle()
    panel.update_from({"phase": "client", "champ_select": None})
    clock[0] += .6
    panel.update_from(draw([LEFT, RIGHT], True))
    assert not panel.expanded
    panel.update_from({"phase": "client", "champ_select": {"bench": [], "team": [], "mine": None}})
    assert not panel.expanded and not panel.search.isHidden()
    panel.update_from(draw([RIGHT]))
    assert panel.expanded and "58.1%" in panel.hero_label.text()


def test_background_draw_waits_for_foreground_and_releases_automatic_panel(setup):
    panel, front, _ = setup
    front[0] = False
    panel.update_from(draw([LEFT]))
    assert not panel.expanded and not panel.body_win.isVisible()
    front[0] = True
    panel.update_from(draw([LEFT]))
    assert panel.expanded
    panel.update_from({"phase": "client", "champ_select": {"bench": [], "team": [], "mine": None}})
    assert not panel.expanded and not panel.search.isHidden() and not panel.info.isHidden()
    panel.toggle()
    panel.update_from(draw([RIGHT]))
    panel.update_from({"phase": "client", "champ_select": {"team": []}})
    assert panel.expanded  # The user opened this panel before the draw.
