"""Read-only local champion fallback must be scoped to this game and respect user choices."""
from pathlib import Path

import pytest

from lolhex.config import Settings
from lolhex.engine import Engine
from lolhex.vision.detector import Offer
from lolhex.vision.window import GameProcess
from lolhex.vision import window


@pytest.fixture
def session(store, tmp_path, monkeypatch):
    engine = Engine(Settings(), home=tmp_path, store=store)
    engine._set(phase="ingame")
    process = GameProcess(71, Path("Game/League of Legends.exe"), 100.0, 1000000000)
    current = [process]
    monkeypatch.setattr(window, "find_game_window", lambda: 31 if current[0] else 0)
    monkeypatch.setattr(window, "game_process", lambda hwnd: current[0] if hwnd else None)
    monkeypatch.setattr(engine, "_prefetch_hero", lambda hid: None)
    return engine, process, current


def test_log_hero_replaces_global_statistics_with_hero_statistics(session):
    engine, process, _ = session
    engine._on_offer(Offer(["1029", "2128", "1373"], [], [], [None] * 3))
    assert engine.state["hero_id"] is None
    assert all(row.get("basis") != "hero_win" for row in engine.state["evaluation"]["slots"])
    assert engine._sync_log_hero(process, "Teemo", 0)
    assert engine.state["hero_id"] == "17"
    assert engine.state["hero_source"] == "log"
    assert all(row.get("basis") == "hero_win" for row in engine.state["evaluation"]["slots"])
    # Unchanged log observations do not repeatedly clear/recalculate a visible offer.
    evaluation = engine.state["evaluation"]
    assert not engine._sync_log_hero(process, "Teemo", 0)
    assert engine.state["evaluation"] is evaluation


@pytest.mark.parametrize("source", ["manual", "ocr", "icon"])
def test_existing_hero_is_not_overwritten_by_fallback(session, source):
    engine, process, _ = session
    if source == "manual":
        engine.set_hero("1")
    else:
        engine._set(hero_id="1", hero_source=source)
    assert not engine._sync_log_hero(process, "Teemo", 0)
    assert engine.state["hero_id"] == "1" and engine.state["hero_source"] == source


def test_user_can_override_log_hero_and_log_cannot_take_it_back(session):
    engine, process, _ = session
    engine._sync_log_hero(process, "Teemo", 0)
    engine.set_hero("1")
    engine._sync_log_hero(process, "Teemo", 0)
    engine._sync_log_hero(None, None, 0)
    assert engine.state["hero_id"] == "1"
    assert engine.state["hero_source"] == "manual" and engine._manual_hero


@pytest.mark.parametrize("alias", [None, "UnknownChampion", "Teem0"])
def test_missing_or_unknown_alias_is_not_guessed(session, alias):
    engine, process, _ = session
    assert not engine._sync_log_hero(process, alias, 0)
    assert engine.state["hero_id"] is None


def test_duplicate_database_alias_is_rejected(session, monkeypatch):
    engine, process, _ = session
    heroes = {**engine.store.heroes, "duplicate": {"alias": "Teemo"}}
    monkeypatch.setattr(type(engine.store), "heroes", property(lambda self: heroes))
    assert not engine._sync_log_hero(process, "Teemo", 0)
    assert engine.state["hero_id"] is None


def test_reader_can_retry_after_observer_enters_game(session):
    engine, process, _ = session
    engine._set(phase="client")
    assert not engine._sync_log_hero(process, "Teemo", 0)
    engine._set(phase="ingame")
    assert engine._sync_log_hero(process, "Teemo", 0)


def test_stale_game_process_and_old_epoch_are_rejected(session):
    engine, process, current = session
    current[0] = GameProcess(process.pid, process.executable, 200.0, 2000000000)
    assert not engine._sync_log_hero(process, "Teemo", 0)
    current[0] = process
    engine._clear_selection()
    assert not engine._sync_log_hero(process, "Teemo", 0)
    assert engine.state["hero_id"] is None


def test_process_disappearance_revokes_only_log_sourced_hero(session):
    engine, process, current = session
    engine._sync_log_hero(process, "Teemo", 0)
    current[0] = None
    assert engine._sync_log_hero(None, None, 0)
    assert engine.state["hero_id"] is None and engine._log_hero_game_key is None


def test_invalidated_log_revokes_fallback_before_next_game(session):
    engine, process, _ = session
    engine._sync_log_hero(process, "Teemo", 0)
    assert engine._sync_log_hero(process, None, 0)
    assert engine.state["hero_id"] is None
    engine._sync_log_hero(process, "Teemo", 0)
    engine._on_game_end()
    assert engine._log_hero_game_key is None
    assert not engine._sync_log_hero(process, "Teemo", 0)
    assert engine.state["hero_id"] is None


def test_stopped_engine_does_not_accept_late_log_result(session):
    engine, process, _ = session
    engine.stop()
    assert not engine._sync_log_hero(process, "Teemo", 0)
    assert engine.state["hero_id"] is None


def test_reader_failure_discards_its_hero_and_stale_recommendation(session):
    engine, process, _ = session
    engine._sync_log_hero(process, "Teemo", 0)
    engine._on_offer(Offer(["1029", "2128", "1373"], [], [], [None] * 3))
    assert engine.state["evaluation"] is not None
    engine._discard_log_hero(0)
    assert engine.state["hero_id"] is None and engine._log_hero_game_key is None
    assert engine.state["offer"] is None and engine.state["evaluation"] is None
    engine.set_hero("1")
    engine._discard_log_hero(0)
    assert engine.state["hero_id"] == "1" and engine.state["hero_source"] == "manual"


def test_old_reader_failure_cannot_clear_a_new_game_hero(session):
    engine, process, _ = session
    engine._clear_selection()
    engine._sync_log_hero(process, "Teemo", 1)
    engine._discard_log_hero(0)
    assert engine.state["hero_id"] == "17" and engine.state["hero_source"] == "log"
