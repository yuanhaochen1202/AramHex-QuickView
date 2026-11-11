"""启动冒烟测试：overlay.run 能走到事件循环（1.0.8/1.0.9 曾因变量名冲突一启动就崩）。"""
import pytest

pytest.importorskip("PySide6")


def test_run_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    from PySide6.QtWidgets import QApplication

    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui import mainwindow, overlay

    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(mainwindow.MainWindow, "check_update", lambda self, manual=False: None)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    s = Settings.load(tmp_path / "config.json")
    s.check_data_updates = False
    # This test stubs the event loop. Do not leave a welcome dialog queued for
    # a later geometry test's real Qt event loop in the same process.
    s.seen_welcome = True
    assert overlay.run(lambda: Engine(s), minimized=True) == 0


def test_version_notice_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    from lolhex import __version__
    from lolhex.config import Settings
    from lolhex.ui.overlay import version_notice
    s = Settings.load(tmp_path / "config.json")
    assert version_notice(s) is None            # 第一次安装：不提示
    assert version_notice(s) is None            # 同一个版本再开：不提示
    s.last_version = "0.0.1"                    # 模拟刚从旧版本更新上来
    assert version_notice(s) == f"已更新到 {__version__}"
    assert version_notice(s) is None            # 只提示一次
    old = tmp_path / "old.json"                 # 老版本留下的设置文件（没有 last_version）
    old.write_text('{"show_winrate": true}', encoding="utf-8")
    assert version_notice(Settings.load(old)) == f"已更新到 {__version__}"


def test_launcher_shows_friendly_error(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("launcher", Path(__file__).parent.parent / "packaging" / "launcher.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    path = launcher._crash_log("Traceback: 测试")
    assert path and "测试" in Path(path).read_text(encoding="utf-8")


def test_blank_capture_warns_once():
    from types import SimpleNamespace
    from lolhex.ui import overlay
    said = []
    ov = SimpleNamespace(visible=True, notify=said.append,
                         engine=SimpleNamespace(snapshot=lambda: {"vision": {"blank": True}}))
    for _ in range(3):
        try:
            overlay.Overlay._tick(ov)
        except AttributeError:
            pass  # 后面画浮窗的部分在这个替身上没有，提醒已经发了
    assert len(said) == 1 and "无边框" in said[0]
