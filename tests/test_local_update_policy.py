"""本地优化版只更新数据：程序升级信号、下载与最终替换入口均不能覆盖补丁。"""
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from lolhex.ui import mainwindow


@pytest.fixture(autouse=True, params=["maintainer", "user"])
def build_channel(monkeypatch, request):
    monkeypatch.setattr(mainwindow.distribution, "UPSTREAM_NOTICES", request.param == "maintainer")


def forbidden(*args, **kwargs):
    pytest.fail("本地优化版不应访问程序升级或安装入口")


@pytest.fixture
def app(monkeypatch):
    # Worker.run() 在当前线程执行，信号也同步送达，不启动网络或后台线程。
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(mainwindow.upstream, "check", lambda: {"status": "current", "version": "1.1.5"})
    yield app


@pytest.mark.parametrize("manual", [False, True])
def test_check_keeps_cloud_and_manual_data_sync_without_program_requests(app, monkeypatch, manual):
    assert mainwindow.PROGRAM_UPDATES_ENABLED is False
    calls, received, done, found = [], [], [], []
    cfg = {"min_version": "99.0", "public": {"enabled": True}}
    monkeypatch.setattr(mainwindow.cloud, "refresh", lambda: calls.append("cloud") or cfg)
    monkeypatch.setattr(mainwindow.update, "check", forbidden)
    monkeypatch.setattr(mainwindow.patch, "prepare", forbidden)
    sync = lambda: calls.append("data") or "统计数据已更新。"
    worker = mainwindow.UpdateWorker("check", strict=manual, data_sync=sync if manual else None)
    worker.cloud.connect(received.append)
    worker.data_done.connect(received.append)
    worker.uptodate.connect(lambda: done.append(True))
    worker.found.connect(found.append)
    worker.failed.connect(forbidden)
    worker.run()
    assert calls == (["cloud", "data"] if manual else ["cloud"])
    assert received == ([cfg, "统计数据已更新。"] if manual else [cfg])
    assert done == [True] and not found


def test_download_worker_cannot_fetch_program_files(app, monkeypatch):
    monkeypatch.setattr(mainwindow.cloud, "refresh", forbidden)
    monkeypatch.setattr(mainwindow.patch, "prepare", forbidden)
    monkeypatch.setattr(mainwindow.update, "download", forbidden)
    worker = mainwindow.UpdateWorker("download", info={"version": "99.0", "url": "https://example.com/app.exe"})
    errors = []
    worker.failed.connect(errors.append)
    worker.patched.connect(forbidden)
    worker.ready.connect(forbidden)
    worker.manual.connect(forbidden)
    worker.run()
    assert errors == [mainwindow.distribution.program_update_note()]


def test_update_signals_and_final_write_paths_are_disabled(monkeypatch):
    monkeypatch.setattr(mainwindow, "UpdateWorker", forbidden)
    monkeypatch.setattr(mainwindow.patch, "launch_apply", forbidden)
    monkeypatch.setattr(mainwindow.update, "launch_installer", forbidden)
    monkeypatch.setattr(mainwindow.webbrowser, "open", forbidden)
    win = SimpleNamespace()  # 禁止路径应在访问窗口、启动进度或下载线程前退出。
    mainwindow.MainWindow._update_found(win, {"version": "99.0"}, manual=True)
    mainwindow.MainWindow._start_update(win)
    mainwindow.MainWindow._apply_patch(win, ["AramHex.exe"], [])
    mainwindow.MainWindow._install(win, "unexpected-setup.exe")
    assert vars(win) == {}


def test_cloud_minimum_version_does_not_prompt_program_upgrade(monkeypatch):
    monkeypatch.setattr(mainwindow.QMessageBox, "exec", forbidden)
    monkeypatch.setattr(mainwindow.update, "is_newer", forbidden)
    win = SimpleNamespace(engine=SimpleNamespace(settings=SimpleNamespace(seen_notice="")))
    mainwindow.MainWindow._cloud_popups(win, {"min_version": "99.0"})
    assert not hasattr(win, "_min_warned")


def test_manual_data_check_still_updates_statistics_and_official_resources():
    calls = []
    store = SimpleNamespace(
        dataset=SimpleNamespace(patch="16.22", snapshot_at="2026-11-06T12:00:00+08:00"),
        data_note={"id": "new-data"},
        sync_error=None,
        sync_data=lambda **kw: calls.append(("statistics", kw)) or True,
        sync_official=lambda **kw: calls.append(("official", kw)) or True,
        official=SimpleNamespace(status=lambda: {"official_revision": 2}),
    )
    win = SimpleNamespace(engine=SimpleNamespace(store=store))
    msg = mainwindow.MainWindow._sync_data_now(win)
    assert calls == [("statistics", {"force": True, "manual": True}), ("official", {"force": True})]
    assert "统计数据已更新" in msg and "官方资料已更新" in msg
    assert win._shown_note == "new-data"


def test_manual_check_message_reports_data_and_program_lock(monkeypatch):
    from test_update_check import FakeWorker, _win
    boxes = []
    monkeypatch.setattr(mainwindow, "UpdateWorker", FakeWorker)
    monkeypatch.setattr(mainwindow.QMessageBox, "information", lambda parent, title, text: boxes.append((title, text)))
    win, _ = _win()
    mainwindow.MainWindow.check_update(win, manual=True)
    worker = win._worker
    worker.data_done.emit(worker.data_sync())
    worker.uptodate.emit()
    worker.finished.emit()
    assert boxes == [(mainwindow.CHECK_UPDATE_LABEL, mainwindow.distribution.program_update_note() + "\n统计数据已是最新。")]
    assert win.btn_check.enabled and win.btn_check.text == "检查数据更新"
