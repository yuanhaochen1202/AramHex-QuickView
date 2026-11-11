"""启动自检必须覆盖真实业务 UI，且不得触碰正在使用的配置或外部服务。"""
import builtins
import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QCheckBox

from lolhex import gui_selftest


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_real_startup_constructs_ui_without_network_or_real_profile(app, monkeypatch, tmp_path):
    import requests
    from lolhex import patch
    from lolhex.config import Settings, app_home
    from lolhex.ui.mainwindow import MainWindow
    from lolhex.ui import single_instance
    from lolhex.vision import window

    live_home = tmp_path / "live-profile"
    live_home.mkdir()
    sentinel = live_home / "config.json"
    sentinel.write_bytes(b'{"sentinel":"must not read or change"}')
    monkeypatch.setenv("ARAMHEX_HOME", str(live_home))
    load = Settings.load
    loaded_profiles = []
    settings_tab = MainWindow._settings_tab
    settings_tabs_checked = []

    def real_load(cls, path=None):
        loaded_profiles.append(app_home())
        settings = load(path)
        assert not hasattr(settings, "install_id") and not hasattr(settings, "send_usage")
        return settings

    def checked_settings_tab(self):
        tab = settings_tab(self)
        assert not hasattr(self, "set_usage")
        assert all("帮助改善软件" not in box.text() and "匿名" not in box.text()
                   for box in tab.findChildren(QCheckBox))
        settings_tabs_checked.append(True)
        return tab

    def forbidden(*args, **kwargs):
        pytest.fail("self-test must not contact game, network, existing instance or live install")

    monkeypatch.setattr(Settings, "load", classmethod(real_load))
    monkeypatch.setattr(MainWindow, "_settings_tab", checked_settings_tab)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(single_instance, "acquire", forbidden)
    monkeypatch.setattr(window, "find_game_window", forbidden)
    monkeypatch.setattr(window, "find_client_window", forbidden)
    monkeypatch.setattr(patch, "install_root", forbidden)
    monkeypatch.setattr(gui_selftest, "_vision_check", lambda: {"ocr_ok": True})
    existing = set(app.topLevelWidgets())

    report_path = tmp_path / "report.json"
    assert gui_selftest.run(str(report_path)) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["success"] and report["startup_ok"] and report["gui_ok"]
    assert report["startup_classes"] == ["Engine", "MainWindow", "Overlay", "Tray"]
    assert report["startup_tabs"] >= 3
    assert settings_tabs_checked == [True]
    assert report["background_tasks_suppressed"]
    assert not report["single_instance_contacted"] and not report["external_test_paths_added"]
    assert loaded_profiles and all(p != live_home and not p.exists() for p in loaded_profiles)
    assert app_home() == live_home
    assert sentinel.read_bytes() == b'{"sentinel":"must not read or change"}'
    assert set(app.topLevelWidgets()) == existing
    assert requests.sessions.Session.request is forbidden
    assert patch.install_root is forbidden


def test_missing_business_dependency_fails_before_ocr(app, monkeypatch, tmp_path):
    original_import = builtins.__import__

    def missing_dependency(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ui.mainwindow" and (globals or {}).get("__name__") == "lolhex.gui_selftest":
            raise ModuleNotFoundError("No module named 'uuid'", name="uuid")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_dependency)
    monkeypatch.setattr(gui_selftest, "_vision_check", lambda: pytest.fail("OCR must not hide startup failure"))
    path = tmp_path / "missing.json"
    assert gui_selftest.run(str(path)) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert not report["success"] and not report["startup_ok"]
    assert "No module named 'uuid'" in report["error"]


def test_partial_constructor_failure_is_reported_and_widgets_removed(app, monkeypatch, tmp_path):
    from lolhex.ui.mainwindow import MainWindow

    def broken_settings_tab(self):
        raise RuntimeError("startup settings construction failed")

    monkeypatch.setattr(MainWindow, "_settings_tab", broken_settings_tab)
    existing = set(app.topLevelWidgets())
    path = tmp_path / "constructor.json"
    assert gui_selftest.run(str(path)) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert not report["success"]
    assert "startup settings construction failed" in report["error"]
    assert set(app.topLevelWidgets()) == existing


def test_qt_event_exception_cannot_produce_success(app, monkeypatch, tmp_path):
    from lolhex.ui.mainwindow import MainWindow
    check_update = MainWindow.check_update

    def broken_event():
        raise RuntimeError("startup event callback failed")

    def schedule_failure(self, *args, **kwargs):
        check_update(self, *args, **kwargs)
        QTimer.singleShot(0, broken_event)

    monkeypatch.setattr(MainWindow, "check_update", schedule_failure)
    path = tmp_path / "event.json"
    assert gui_selftest.run(str(path)) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert not report["success"] and not report["startup_ok"]
    assert "startup event callback failed" in report["error"]
