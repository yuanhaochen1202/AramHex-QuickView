"""原项目版本信息只能提示评估合并，不能成为安装指令。"""
from types import SimpleNamespace

import pytest

from lolhex import distribution, upstream


@pytest.fixture(autouse=True)
def maintainer_channel(monkeypatch):
    monkeypatch.setattr(distribution, "UPSTREAM_NOTICES", True)


class Response:
    def __init__(self, info):
        self.info = info
    def raise_for_status(self):
        pass
    def json(self):
        return self.info


def test_metadata_discards_install_assets_and_uses_upstream_baseline(monkeypatch):
    import lolhex
    monkeypatch.setattr(lolhex, "__version__", "99.0.0")
    requested = []
    def get(url, **kwargs):
        requested.append(url)
        return Response({"version": "1.1.6", "setup": {"url": "https://unwanted.example/app.exe"},
                         "script": "do not run", "min_version": "99.0"})
    monkeypatch.setattr(upstream.requests, "get", get)
    assert upstream.check() == {"status": "newer", "version": "1.1.6"}
    assert requested == [upstream.SOURCES[0]]


def test_fallback_uses_only_pinned_metadata_endpoints(monkeypatch):
    requested = []
    def get(url, **kwargs):
        requested.append(url)
        if "api.github.com" not in url:
            raise upstream.requests.ConnectionError("offline")
        return Response({"tag_name": "v1.1.6", "html_url": "https://unwanted.example", "assets": []})
    monkeypatch.setattr(upstream.requests, "get", get)
    assert upstream.check() == {"status": "newer", "version": "1.1.6"}
    assert requested == list(upstream.SOURCES)


@pytest.mark.parametrize("info", [{"version": "1.1.6-test.1"}, {"version": "<script>"},
                                 {"tag_name": "v1.1.6", "prerelease": True},
                                 {"version": "1.1.6", "draft": True}, [], None])
def test_invalid_or_prerelease_metadata_is_not_a_release(monkeypatch, info):
    monkeypatch.setattr(upstream.requests, "get", lambda *a, **k: Response(info))
    assert upstream.check() == {"status": "unavailable"}


def test_unavailable_version_check_is_not_a_database_failure(monkeypatch):
    def offline(*a, **k):
        raise upstream.requests.Timeout("offline")
    monkeypatch.setattr(upstream.requests, "get", offline)
    assert upstream.check() == {"status": "unavailable"}


def test_worker_reports_metadata_without_calling_program_updater(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import mainwindow
    app = QApplication.instance() or QApplication([])
    calls, found, errors, notices = [], [], [], []
    def forbidden(*a, **k):
        pytest.fail("read-only upstream metadata must never call a program updater")
    monkeypatch.setattr(mainwindow.cloud, "refresh", lambda: {})
    monkeypatch.setattr(upstream, "check", lambda: {"status": "newer", "version": "1.1.6"})
    monkeypatch.setattr(mainwindow.update, "check", forbidden)
    monkeypatch.setattr(mainwindow.update, "download", forbidden)
    monkeypatch.setattr(mainwindow.patch, "prepare", forbidden)
    worker = mainwindow.UpdateWorker("check", data_sync=lambda: calls.append("data") or "数据已更新",
                                    check_program_notice=True)
    worker.upstream_checked.connect(notices.append)
    worker.found.connect(found.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert calls == ["data"] and not errors and not found
    assert notices == [{"status": "newer", "version": "1.1.6"}]


def test_data_worker_defaults_to_no_program_notice(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import mainwindow
    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(mainwindow.cloud, "refresh", lambda: calls.append("cloud") or {})
    monkeypatch.setattr(upstream, "check", lambda: pytest.fail("数据刷新不应检查原作者程序版本"))
    worker = mainwindow.UpdateWorker("check", data_sync=lambda: calls.append("data") or "数据已更新")
    worker.run()
    assert calls == ["cloud", "data"]


def test_maintainer_checks_program_notice_only_on_first_startup_call(monkeypatch):
    pytest.importorskip("PySide6")
    from lolhex.ui import mainwindow
    from test_update_check import FakeWorker, _win
    monkeypatch.setattr(mainwindow.distribution, "CHANNEL", "maintainer")
    monkeypatch.setattr(mainwindow, "UpdateWorker", FakeWorker)
    win, _ = _win()
    mainwindow.MainWindow.check_update(win)
    first = win._worker
    assert first.check_program_notice and first.upstream_checked.slots
    first.running = False
    first.finished.emit()
    mainwindow.MainWindow.check_update(win)
    assert not win._worker.check_program_notice and not win._worker.upstream_checked.slots
    win._worker.running = False
    win._worker.finished.emit()
    mainwindow.MainWindow.check_update(win, manual=True)
    assert not win._worker.check_program_notice and win._worker.data_sync


def test_newer_version_notice_is_once_and_deferred_during_game():
    pytest.importorskip("PySide6")
    from lolhex.ui.mainwindow import MainWindow
    saved, shown = [], []
    settings = SimpleNamespace(extra={}, save=lambda: saved.append(True))
    win = SimpleNamespace(engine=SimpleNamespace(settings=settings), _busy=lambda: True,
                          _toast=shown.append, _update_info=None)
    info = {"status": "newer", "version": "1.1.6"}
    MainWindow._upstream_result(win, info)
    MainWindow._upstream_result(win, info)
    assert len(saved) == 1 and not shown
    assert "可交给 GPT 评估合并" in win._deferred_toast
    assert win._update_info is None
    MainWindow._upstream_result(win, info, manual=True)
    assert len(saved) == 1 and "不会自动替换" in win._upstream_msg


def test_manual_data_check_does_not_check_upstream_program(monkeypatch):
    pytest.importorskip("PySide6")
    from lolhex.ui import mainwindow
    from test_update_check import FakeWorker, _win
    boxes = []
    monkeypatch.setattr(mainwindow, "UpdateWorker", FakeWorker)
    monkeypatch.setattr(mainwindow.QMessageBox, "information", lambda parent, title, text: boxes.append(text))
    win, _ = _win()
    mainwindow.MainWindow.check_update(win, manual=True)
    worker = win._worker
    assert not worker.check_program_notice and not worker.upstream_checked.slots
    worker.data_done.emit("统计数据已是最新。")
    worker.upstream_checked.emit({"status": "newer", "version": "1.1.6"})
    worker.uptodate.emit()
    assert len(boxes) == 1 and "统计数据已是最新" in boxes[0] and "原项目有新版 1.1.6" not in boxes[0]
    assert win._update_info is None


def test_user_cannot_request_metadata_even_if_checker_is_called_directly(monkeypatch):
    monkeypatch.setattr(distribution, "UPSTREAM_NOTICES", False)
    monkeypatch.setattr(upstream.requests, "get", lambda *a, **k: pytest.fail("用户版不能查询原项目版本"))
    assert upstream.check() == {"status": "disabled"}


@pytest.mark.parametrize("manual", [False, True])
def test_user_worker_keeps_data_but_never_calls_upstream(monkeypatch, manual):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from lolhex.ui import mainwindow
    app = QApplication.instance() or QApplication([])
    calls, notices, done = [], [], []
    def forbidden(*a, **k):
        pytest.fail("用户版不能调用原项目检查器或程序升级器")
    monkeypatch.setattr(distribution, "UPSTREAM_NOTICES", False)
    monkeypatch.setattr(mainwindow.cloud, "refresh", lambda: calls.append("cloud") or {})
    monkeypatch.setattr(upstream, "check", forbidden)
    monkeypatch.setattr(mainwindow.update, "check", forbidden)
    monkeypatch.setattr(mainwindow.update, "download", forbidden)
    monkeypatch.setattr(mainwindow.patch, "prepare", forbidden)
    worker = mainwindow.UpdateWorker("check", strict=manual,
                                    data_sync=(lambda: calls.append("data") or "数据已更新") if manual else None)
    worker.upstream_checked.connect(notices.append)
    worker.uptodate.connect(lambda: done.append(True))
    worker.failed.connect(forbidden)
    worker.run()
    assert calls == (["cloud", "data"] if manual else ["cloud"])
    assert done == [True] and not notices


def test_user_ignores_queued_upstream_notice_and_manual_dialog_has_no_release(monkeypatch):
    pytest.importorskip("PySide6")
    from lolhex.ui import mainwindow
    from test_update_check import FakeWorker, _win
    monkeypatch.setattr(distribution, "UPSTREAM_NOTICES", False)
    empty = SimpleNamespace()
    mainwindow.MainWindow._upstream_result(empty, {"status": "newer", "version": "99.0"})
    assert vars(empty) == {}
    boxes = []
    monkeypatch.setattr(mainwindow, "UpdateWorker", FakeWorker)
    monkeypatch.setattr(mainwindow.QMessageBox, "information", lambda parent, title, text: boxes.append(text))
    win, _ = _win()
    mainwindow.MainWindow.check_update(win, manual=True)
    worker = win._worker
    assert not worker.upstream_checked.slots
    worker.data_done.emit("统计数据已是最新。")
    worker.upstream_checked.emit({"status": "newer", "version": "99.0"})
    worker.uptodate.emit()
    assert len(boxes) == 1 and "统计数据已是最新" in boxes[0] and "用户版" in boxes[0]
    assert "99.0" not in boxes[0] and "GPT" not in boxes[0]
