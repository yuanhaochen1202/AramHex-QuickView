"""用户版的数据检查、提示去重和原版程序更新信号隔离。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from lolhex.ui import mainwindow  # noqa: E402
from lolhex.ui.mainwindow import MainWindow  # noqa: E402


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *a):
        for fn in self.slots:
            fn(*a)


class FakeWorker:
    started = []

    def __init__(self, mode, info=None, parent=None, strict=False, data_sync=None, check_program_notice=False):
        self.mode, self.strict, self.running, self.data_sync = mode, strict, False, data_sync
        self.check_program_notice = check_program_notice
        self.found, self.uptodate, self.failed, self.finished, self.data_done, self.cloud = (FakeSignal() for _ in range(6))
        self.upstream_checked = FakeSignal()

    def start(self):
        FakeWorker.started.append(self)
        self.running = True

    def isRunning(self):
        return self.running

    def wait(self, ms):
        return True

    def deleteLater(self):
        self.deleted = True


class FakeButton:
    def __init__(self):
        self.enabled, self.text = True, ""

    def setEnabled(self, v):
        self.enabled = v

    def setText(self, t):
        self.text = t


def _win():
    shown = []
    win = SimpleNamespace(
        engine=SimpleNamespace(settings=SimpleNamespace()),
        _worker=None, _update_info=None, btn_check=FakeButton(), btn_update=FakeButton(),
        update_text=SimpleNamespace(setText=lambda t: None),
        update_bar=SimpleNamespace(show=lambda: None),
        tray=SimpleNamespace(showMessage=lambda *a: shown.append(a[1])),
    )
    win.check_update = lambda manual=False: MainWindow.check_update(win, manual)
    win._retire_when_done = lambda attr, worker: MainWindow._retire_when_done(win, attr, worker)
    win._sync_data_now = lambda: "统计数据已是最新。"
    win._data_line = lambda: MainWindow._data_line(win)
    win._apply_cloud = lambda cfg: None
    win._upstream_result = lambda info, manual=False: MainWindow._upstream_result(win, info, manual)
    win._in_game = lambda: False
    win._toast = lambda t: MainWindow._toast(win, t)
    win._update_found = lambda info, manual=False: MainWindow._update_found(win, info, manual)
    win._check_failed = lambda msg: shown.append("失败：" + msg)
    win._start_update = lambda: shown.append("开始下载")
    return win, shown


@pytest.fixture
def boxes(monkeypatch):
    said = []
    monkeypatch.setattr(mainwindow.distribution, "CHANNEL", "user")
    monkeypatch.setattr(mainwindow.distribution, "UPSTREAM_NOTICES", False)
    monkeypatch.setattr(mainwindow, "UpdateWorker", FakeWorker)
    FakeWorker.started = []
    monkeypatch.setattr(mainwindow.QMessageBox, "information", lambda parent, title, text: said.append(text))
    return said


def test_busy_worker_deduplicates_data_refresh_and_program_runs_once(boxes):
    win, _ = _win()
    program_checks = []
    win._check_custom_updates = lambda *, startup: program_checks.append(startup)
    win.check_update()
    win.check_update()
    assert len(FakeWorker.started) == 1 and not boxes

    win.check_update(manual=True)
    assert boxes == ["正在检查数据更新，请稍候。"]
    win._worker.finished.emit()
    win.check_update()
    assert len(FakeWorker.started) == 2 and program_checks == [True]
    assert all(not worker.check_program_notice for worker in FakeWorker.started)


def test_manual_data_check_reports_success_and_errors(boxes):
    win, shown = _win()
    program_checks = []
    win._check_custom_updates = lambda **kwargs: program_checks.append(kwargs)
    win.check_update(manual=True)
    worker = win._worker
    assert worker.strict and not win.btn_check.enabled
    worker.data_done.emit(worker.data_sync())
    worker.uptodate.emit()
    worker.finished.emit()
    assert boxes == [mainwindow.distribution.program_update_note() + "\n统计数据已是最新。"]
    assert win.btn_check.enabled and win.btn_check.text == mainwindow.CHECK_UPDATE_LABEL
    assert win._worker is None and worker.deleted

    win.check_update(manual=True)
    win._worker.failed.emit("连接超时")
    win._worker.finished.emit()
    assert shown == ["失败：连接超时"] and win.btn_check.enabled
    assert not program_checks


@pytest.mark.parametrize("manual", [True, False])
def test_upstream_signal_cannot_offer_download(boxes, monkeypatch, manual):
    win, shown = _win()
    monkeypatch.setattr(mainwindow.QMessageBox, "question", lambda *args: pytest.fail("原版更新触发了下载提示"))
    win.check_update(manual=manual)
    worker = win._worker
    for info in ({"version": "9.9.9", "file": "setup.exe", "url": "https://example.com/setup.exe"},
                 {"version": "9.9.9", "file": None, "mirror": True, "page": "https://example.com"}):
        worker.found.emit(info)
    assert not boxes and not shown and win._update_info is None
    assert win.btn_update.text == ""


def test_queued_cloud_data_excludes_notices_and_software_rollout():
    win = SimpleNamespace(engine=SimpleNamespace(), _deferred_cloud=None, _busy=lambda: True,
                          _apply_cloud_settings=lambda cfg: None, recheck_rank_if_newer=lambda cfg: None)
    MainWindow._apply_cloud(win, {
        "notice": {"id": "n1", "text": "公告"},
        "min_version": "9.9.9",
        "update": {"rollout_version": "9.9.9", "rollout_percent": 0},
        "augments": {"aliases": {"1234": ["旧名"]}},
    })
    queued = win._deferred_cloud
    assert queued["notice"] == {} and queued["min_version"] == ""
    assert queued["update"]["rollout_version"] == "" and queued["update"]["rollout_percent"] == 100
    assert queued["augments"]["aliases"] == {"1234": ["旧名"]}


def test_data_prompt_waits_for_game_and_modals_then_asks_once(monkeypatch):
    snapshot = {"phase": "ingame", "data": {"data_offer": {"id": "d1", "text": "新版统计"}}}
    win = SimpleNamespace(engine=SimpleNamespace(snapshot=lambda: snapshot), _popup_open=False,
                          _deferred_cloud=None, _deferred_toast=None, _shown_note=None, _asked_offers=set())
    win._in_game = lambda: MainWindow._in_game(win)
    win._busy = lambda: MainWindow._busy(win)
    win._modal_open = lambda: MainWindow._modal_open(win)
    prompts = []
    monkeypatch.setattr(mainwindow.QMessageBox, "question",
                        lambda parent, title, text: prompts.append(text) or mainwindow.QMessageBox.No)
    monkeypatch.setattr(mainwindow.QApplication, "activeModalWidget", staticmethod(lambda: None))
    MainWindow._notify_tick(win)
    snapshot.update(phase="client", champ_select={"bench": []})
    MainWindow._notify_tick(win)
    snapshot["champ_select"] = None
    win._popup_open = True
    MainWindow._notify_tick(win)
    win._popup_open = False
    monkeypatch.setattr(mainwindow.QApplication, "activeModalWidget", staticmethod(lambda: object()))
    MainWindow._notify_tick(win)
    assert prompts == [] and not win._asked_offers

    monkeypatch.setattr(mainwindow.QApplication, "activeModalWidget", staticmethod(lambda: None))
    MainWindow._notify_tick(win)
    MainWindow._notify_tick(win)
    assert len(prompts) == 1 and "新版统计" in prompts[0] and win._asked_offers == {"d1"}
