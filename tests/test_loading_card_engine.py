"""卡片视觉结果经本局、窗口、前台复核；慢 OCR 不能锁住日志线程。"""
import copy
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
import numpy as np
import pytest
from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision import capture, window
from lolhex.vision.window import GameProcess


@pytest.fixture
def scan(store, tmp_path, monkeypatch):
    engine = Engine(Settings(), home=tmp_path, store=store)
    rect = window.Rect(20, 30, 1920, 1080)
    process = GameProcess(71, Path('Game/League of Legends.exe'), 100.0, 1000000000)
    roster = {'game_key': process.key, 'teams': [{'side': 100, 'members': [{'slot': 0, 'hero_id': '17'}]},
                                               {'side': 200, 'members': []}], 'complete': False}
    engine._set(phase='ingame', loading_roster=copy.deepcopy(roster))
    context = SimpleNamespace(engine=engine, rect=rect, process=process, foreground=True, hwnd=31,
                              calls=[], after_step=lambda: None, fail=False,
                              cards=[{'hero_id': '17', 'box': [300, 850, 480, 884]}])
    monkeypatch.setattr(window, 'find_game_window', lambda: context.hwnd)
    monkeypatch.setattr(window, 'game_process', lambda hwnd: context.process if hwnd else None)
    monkeypatch.setattr(window, 'client_rect', lambda hwnd: context.rect)
    monkeypatch.setattr(window, 'is_foreground', lambda hwnd: context.foreground)
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    def grab(rect):
        context.calls.append('capture')
        return frame
    monkeypatch.setattr(capture, 'grab', grab)
    def recognize(crop):
        context.calls.append('ocr')
        return []
    class Locator:
        def step(self, image, current_roster, recognize):
            assert image is frame
            context.calls.append('step')
            recognize(image[:8, :8])
            context.after_step()
            if context.fail:
                raise RuntimeError('synthetic OCR error')
            return list(context.cards)
    fake = ModuleType('lolhex.vision.loadingcards')
    fake.LoadingCardLocator = Locator
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    from lolhex.vision.ocr import OCR
    monkeypatch.setattr(OCR, 'get', lambda: SimpleNamespace(recognize=recognize))
    return context


def test_scan_publishes_partial_cards_with_current_game_and_physical_rectangle(scan):
    assert scan.engine._scan_loading_cards(scan.rect, scan.hwnd)
    assert scan.calls == ['capture', 'step', 'ocr']
    assert scan.engine.state['loading_cards'] == {
        'game_key': scan.process.key, 'rect': [20, 30, 1920, 1080], 'cards': scan.cards}


@pytest.mark.parametrize('invalid', ['no_roster', 'other_game', 'no_process'])
def test_no_valid_current_loading_roster_does_not_capture_or_ocr(scan, invalid):
    scan.engine._set(loading_cards={'old': True})
    if invalid == 'no_roster':
        scan.engine._set(loading_roster=None)
    elif invalid == 'other_game':
        scan.engine.state['loading_roster']['game_key'] = 'old-game'
    else:
        scan.process = None
    assert not scan.engine._scan_loading_cards(scan.rect, scan.hwnd)
    assert not scan.calls and scan.engine.state['loading_cards'] is None


@pytest.mark.parametrize('race', ['loading_ended', 'roster_changed', 'process_changed', 'window_changed',
                                  'moved', 'resized', 'background', 'stopped', 'game_ended'])
def test_slow_scan_results_are_discarded_when_context_changes(scan, race):
    original_rect, original_hwnd = scan.rect, scan.hwnd
    def after_step():
        if race == 'loading_ended':
            scan.engine._set(loading_roster=None)
        elif race == 'roster_changed':
            scan.engine.state['loading_roster']['teams'][0]['members'].append({'slot': 1, 'hero_id': '1'})
        elif race == 'process_changed':
            scan.process = GameProcess(71, Path('Game/League of Legends.exe'), 200.0, 2000000000)
        elif race == 'window_changed':
            scan.hwnd = 32
        elif race == 'moved':
            scan.rect = window.Rect(21, 30, 1920, 1080)
        elif race == 'resized':
            scan.rect = window.Rect(20, 30, 1280, 720)
        elif race == 'background':
            scan.foreground = False
        elif race == 'stopped':
            scan.engine.stop()
        elif race == 'game_ended':
            scan.engine._on_game_end()
    scan.after_step = after_step
    assert scan.engine._scan_loading_cards(original_rect, original_hwnd)
    assert scan.engine.state['loading_cards'] is None


def test_scan_error_clears_old_positions_and_resets_locator(scan):
    scan.engine._set(loading_cards={'old': True})
    scan.fail = True
    assert scan.engine._scan_loading_cards(scan.rect, scan.hwnd)
    assert scan.engine.state['loading_cards'] is None
    assert scan.engine._loading_card_locator is None


def test_loading_end_can_be_published_while_card_ocr_is_still_running(scan):
    entered, release, changed = threading.Event(), threading.Event(), threading.Event()
    def slow_step():
        entered.set()
        assert release.wait(3), 'test did not release synthetic OCR'
    scan.after_step = slow_step
    worker = threading.Thread(target=scan.engine._scan_loading_cards, args=(scan.rect, scan.hwnd), daemon=True)
    def finish_loading():
        scan.engine._set(loading_roster=None)
        changed.set()
    writer = threading.Thread(target=finish_loading, daemon=True)
    try:
        worker.start()
        assert entered.wait(3)
        writer.start()
        assert changed.wait(1), 'card OCR held engine lock and delayed log reader'
    finally:
        release.set()
        worker.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert not worker.is_alive() and not writer.is_alive()
    assert scan.engine.state['loading_roster'] is None
    assert scan.engine.state['loading_cards'] is None
