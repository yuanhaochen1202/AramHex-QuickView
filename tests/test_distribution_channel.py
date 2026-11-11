"""同一源码构建两种渠道；用户版安装包不能启用原作者版本通知。"""
import importlib.util
import sys
from types import ModuleType, SimpleNamespace

import pytest

from lolhex import __version__, distribution


def load_channel(monkeypatch, channel):
    if channel is None:
        monkeypatch.delitem(sys.modules, "lolhex._distribution", raising=False)
    else:
        override = ModuleType("lolhex._distribution")
        override.CHANNEL, override.CUSTOM_PROGRAM_REPO = channel, ""
        monkeypatch.setitem(sys.modules, "lolhex._distribution", override)
    spec = importlib.util.spec_from_file_location("lolhex._test_distribution", distribution.__file__)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("channel,notices,label", [(None, False, "用户版"),
                                                  ("maintainer", True, "维护版"),
                                                  ("user", False, "用户版")])
def test_build_override_selects_channel_without_repo_fallback(monkeypatch, channel, notices, label):
    result = load_channel(monkeypatch, channel)
    assert result.CHANNEL == (channel or "user")
    assert result.UPSTREAM_NOTICES is notices and result.CHANNEL_LABEL == label
    assert result.CUSTOM_PROGRAM_REPO == ""
    assert label in result.program_update_note()


def test_invalid_build_channel_fails_instead_of_enabling_upstream(monkeypatch):
    with pytest.raises(ValueError, match="未知构建渠道"):
        load_channel(monkeypatch, "user-typo")


@pytest.mark.parametrize("channel", ["maintainer", "user"])
def test_window_title_and_upstream_status_follow_build_channel(monkeypatch, channel, tmp_path, store):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui.mainwindow import MainWindow
    built = load_channel(monkeypatch, channel)
    for key in ("CHANNEL", "CHANNEL_LABEL", "UPSTREAM_NOTICES"):
        monkeypatch.setattr(distribution, key, getattr(built, key))
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(QTimer, "singleShot", lambda *a: None)
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    settings.seen_welcome = True
    engine = Engine(settings, home=tmp_path, store=store)
    win = MainWindow(engine, SimpleNamespace(set_visible=lambda *a: None))
    try:
        suffix = "（维护版）" if channel == "maintainer" else ""
        assert win.windowTitle() == f"海克斯大乱斗助手 v{__version__}{suffix}"
        assert hasattr(win, "upstream_status") is built.UPSTREAM_NOTICES
        labels = [label.text() for label in win.findChildren(QLabel)]
        assert distribution.program_update_note() in labels
        if channel == "user":
            assert not any("原项目基础版本" in text or "GPT" in text for text in labels)
    finally:
        for timer in win.findChildren(QTimer):
            timer.stop()
        win.hide()
        win.deleteLater()
        engine.stop()
        app.processEvents()
