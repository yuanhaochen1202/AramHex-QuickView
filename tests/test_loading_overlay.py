"""逐卡胜率的实际 Qt 绘制、物理坐标、缺数据和生命周期。"""
import copy
import os
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtWidgets import QApplication
from lolhex.ui import overlay
from lolhex.ui.loadingoverlay import LoadingLayer
from lolhex.vision import window


def snapshot(complete=True, rect=None):
    rect = rect or window.Rect(0, 0, 1920, 1080)
    teams, cards = [], []
    unit = min(rect.width / 1920, rect.height / 1080)
    offset = (rect.width - 1920 * unit) / 2
    for t, label in enumerate(('我方', '敌方')):
        members = [{'slot': i, 'hero_id': str(t * 5 + i + 1), 'name': '英雄' + str(i),
                    'win': .44 + (t * 5 + i) * .014, 'is_local': t == 0 and i == 0} for i in range(5)]
        teams.append({'label': label, 'side': 100 + t * 100, 'members': members})
        for i, member in enumerate(members):
            x, y = offset + (270 + i * 288) * unit, (870 if t == 0 else 350) * unit
            cards.append({'hero_id': member['hero_id'], 'box': [round(x), round(y), round(x + 180 * unit), round(y + 34 * unit)]})
    return {'phase': 'ingame', 'loading_roster': {'game_key': 'current-game', 'complete': complete, 'teams': teams},
            'loading_cards': {'game_key': 'current-game', 'rect': [rect.left, rect.top, rect.width, rect.height], 'cards': cards},
            'data': {'dataset_patch': '16.22', 'band_effective': 'mid'}}


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    return app


@pytest.mark.parametrize('size,dpr', [((1280, 720), 1), ((1920, 1080), 1.25), ((2560, 1440), 1.5),
                                     ((3840, 2160), 1.5), ((3440, 1440), 2)])
def test_ten_rates_draw_only_inside_individual_click_through_badges(app, monkeypatch, size, dpr):
    monkeypatch.setattr(window, 'IS_WINDOWS', False)
    monkeypatch.setattr(overlay, '_dpr', lambda: dpr)
    layer = LoadingLayer()
    rect = window.Rect(-300, 200, *size)
    snap = snapshot(rect=rect)
    try:
        layer.update_from(snap, rect, 7)
        app.processEvents()
        assert layer.isVisible()
        assert layer.windowFlags() & Qt.WindowTransparentForInput
        assert layer.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert layer.testAttribute(Qt.WA_ShowWithoutActivating)
        image = QImage(round(layer.width() * dpr), round(layer.height() * dpr), QImage.Format_ARGB32)
        image.setDevicePixelRatio(dpr)
        image.fill(0)
        layer.render(image)
        pixels = np.frombuffer(image.constBits(), dtype=np.uint32).reshape(image.height(), image.bytesPerLine() // 4)
        expected = LoadingLayer.badges(snap, rect)
        assert len(expected) == 10
        regions = window.drawn_rects()
        assert len(regions) == 10
        for member, box in expected:
            x, y = box.left - layer.origin.x(), box.top - layer.origin.y()
            area = pixels[max(0, y):y + box.height, max(0, x):x + box.width]
            assert np.count_nonzero(area == overlay.win_color(member['win']).rgb()) > 3
            assert any(abs(r.left - box.left) <= 4 and abs(r.top - box.top) <= 4 and
                       r.width <= box.width + 6 and r.height <= box.height + 6 for r in regions)
        assert not np.any(pixels[pixels.shape[0] // 2] >> 24)
        assert all(r.height < rect.height * .06 for r in regions)
        folder = os.environ.get('ARAMHEX_QA_IMAGES')
        if folder and size in ((1920, 1080), (3840, 2160)):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert image.save(str(Path(folder) / f'loading-cards-{size[0]}-dpr{dpr:g}.png'))
    finally:
        layer.hide()
        layer.close()
        app.processEvents()


def test_visual_positions_determine_order_independently_of_team_and_slot():
    rect = window.Rect(0, 0, 1920, 1080)
    snap = snapshot(rect=rect)
    first = LoadingLayer.badges(snap, rect)
    assert first[0][0]['hero_id'] == '1' and first[0][1].top > first[5][1].top
    a, b = snap['loading_cards']['cards'][0], snap['loading_cards']['cards'][7]
    a['box'], b['box'] = b['box'], a['box']
    snap['loading_roster']['teams'].reverse()
    moved = {member['hero_id']: box for member, box in LoadingLayer.badges(snap, rect)}
    assert moved['1'] == first[7][1] and moved['8'] == first[0][1]


def test_stale_game_geometry_and_unknown_heroes_are_not_drawn():
    rect = window.Rect(0, 0, 1920, 1080)
    snap = snapshot(rect=rect)
    assert not LoadingLayer.badges(snap, window.Rect(1, 0, 1920, 1080))
    assert not LoadingLayer.badges(snap, window.Rect(0, 0, 1280, 720))
    snap['loading_cards']['game_key'] = 'previous-game'
    assert not LoadingLayer.badges(snap, rect)
    snap['loading_cards']['game_key'] = 'current-game'
    snap['loading_cards']['cards'] = [{'hero_id': 'unknown', 'box': [0, 0, 180, 34]}]
    assert not LoadingLayer.badges(snap, rect)


@pytest.mark.parametrize('box', [None, [], [1, 2, 3], [0, 0, float('nan'), 34],
                                 [-1, 2, 180, 36], [0, 0, 1921, 34], [20, 20, 10, 34]])
def test_invalid_visual_boxes_are_discarded(box):
    snap = snapshot()
    snap['loading_cards']['cards'] = [{'hero_id': '1', 'box': box}]
    assert not LoadingLayer.badges(snap, window.Rect(0, 0, 1920, 1080))


def test_loading_exit_background_and_switch_do_not_leave_old_heroes(app, monkeypatch):
    monkeypatch.setattr(window, 'IS_WINDOWS', False)
    layer = LoadingLayer()
    rect = window.Rect(0, 0, 1920, 1080)
    snap = snapshot(rect=rect)
    try:
        layer.update_from(snap, rect)
        app.processEvents()
        assert layer.isVisible()
        layer.update_from(snap, None)
        assert not layer.isVisible() and not layer.snapshot
        layer.update_from(snap, rect)
        assert layer.isVisible()
        layer.update_from({**snap, 'loading_roster': None}, rect)
        assert not layer.isVisible() and not layer.snapshot
        next_game = copy.deepcopy(snap)
        next_game['loading_roster']['game_key'] = 'new-game'
        layer.update_from(next_game, rect)
        assert not layer.isVisible()
        expired = window.time.monotonic() + window.DRAWN_LINGER_S + 1
        with monkeypatch.context() as patch:
            patch.setattr(window.time, 'monotonic', lambda: expired)
            assert not window.drawn_rects()
    finally:
        layer.close()


def test_missing_and_invalid_rates_are_not_invented():
    assert LoadingLayer.member_text(None)[:2] == ('—', '')
    for value in (None, float('nan'), float('inf'), -1, 1.1, '0.5', True):
        assert LoadingLayer.member_text({'hero_id': '1', 'name': '提莫', 'win': value})[:2] == ('提莫', '暂无数据')
    assert LoadingLayer.member_text({'hero_id': '1', 'name': '提莫', 'win': 0})[:2] == ('提莫', '0.0%')
    assert LoadingLayer.member_text({'hero_id': '1', 'name': '提莫', 'win': .5, 'is_local': True})[:2] == ('提莫 · 我', '50.0%')


def test_partial_roster_and_positions_show_first_hero_immediately(app, monkeypatch):
    monkeypatch.setattr(window, 'IS_WINDOWS', False)
    layer = LoadingLayer()
    snap = snapshot(False)
    teams = snap['loading_roster']['teams']
    teams[0]['members'] = teams[0]['members'][:1]
    teams[1]['members'] = []
    snap['loading_cards']['cards'] = snap['loading_cards']['cards'][:1]
    rect = window.Rect(0, 0, 1920, 1080)
    try:
        layer.update_from(snap, rect)
        app.processEvents()
        assert layer.isVisible() and not layer.grab().isNull()
        assert len(layer._badges) == 1
        teams[1]['members'] = [{'slot': 2, 'hero_id': '88', 'name': '新英雄', 'win': None}]
        layer.update_from(snap, rect)
        assert len(layer._badges) == 1
        snap['loading_cards']['cards'].append({'hero_id': '88', 'box': [846, 350, 1026, 384]})
        layer.update_from(snap, rect)
        assert len(layer._badges) == 2
        assert LoadingLayer.member_text(layer._badges[1][0])[:2] == ('新英雄', '暂无数据')
    finally:
        layer.close()


def test_only_two_teams_and_valid_slots_are_drawn():
    assert not LoadingLayer.rows({'teams': [{'members': []}]})
    roster = snapshot()['loading_roster']
    roster['teams'][0]['members'] += [{'slot': 8, 'name': 'ignore'}, {'slot': '1', 'name': 'ignore'}]
    assert len(LoadingLayer.rows(roster)[0][1]) == 5


@pytest.mark.parametrize('phase', ['client', 'ingame'])
def test_overlay_tick_only_shows_current_game_loading_badges_in_foreground(app, monkeypatch, phase):
    monkeypatch.setattr(window, 'IS_WINDOWS', False)
    monkeypatch.setattr(window, 'find_game_window', lambda: 1)
    monkeypatch.setattr(window, 'client_rect', lambda hwnd: window.Rect(0, 0, 1920, 1080))
    foreground = [True]
    monkeypatch.setattr(window, 'is_foreground', lambda hwnd: foreground[0])
    process = [SimpleNamespace(key='current-game')]
    monkeypatch.setattr(window, 'game_process', lambda hwnd: process[0])
    snap = snapshot()
    snap['phase'] = phase
    ov = overlay.Overlay.__new__(overlay.Overlay)
    ov.engine = SimpleNamespace(snapshot=lambda: snap)
    ov.badges = SimpleNamespace(update_from=lambda *a: None, hide=lambda: None)
    ov.champ = SimpleNamespace(update_from=lambda *a: None, hide=lambda: None)
    ov.loading = LoadingLayer()
    ov.autoshot = SimpleNamespace(tick=lambda *a: None)
    ov.panel = SimpleNamespace(update_from=lambda *a: None, auto_hide=lambda *a: None,
                               isVisible=lambda: True, place=lambda *a, **kw: None, hide=lambda: None)
    try:
        ov._tick()
        assert ov.loading.isVisible()
        foreground[0] = False
        ov._tick()
        assert not ov.loading.isVisible()
        foreground[0] = True
        ov._tick()
        assert ov.loading.isVisible()
        process[0] = SimpleNamespace(key='new-game')
        ov._tick()
        assert not ov.loading.isVisible()
        process[0] = None
        ov._tick()
        assert not ov.loading.isVisible()
        process[0] = SimpleNamespace(key='current-game')
        ov._tick()
        assert ov.loading.isVisible()
        snap['loading_roster'] = None
        ov._tick()
        assert not ov.loading.isVisible()
        snap.update(snapshot())
        ov._tick()
        ov.set_visible(False)
        assert not ov.loading.isVisible()
    finally:
        ov.loading.close()
