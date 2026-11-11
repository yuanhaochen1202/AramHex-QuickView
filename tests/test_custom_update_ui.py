"""启动一次检查；用户版仅在明确确认后下载、安装，无常驻自动重试。"""
from types import MethodType, SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from lolhex.ui import mainwindow as ui


class Signal:
    def __init__(self): self.slots = []
    def connect(self, callback): self.slots.append(callback)
    def emit(self, *args):
        for callback in list(self.slots): callback(*args)


class Widget:
    def __init__(self): self.text, self.enabled, self.visible = "", True, False
    def setText(self, text): self.text = text
    def setEnabled(self, enabled): self.enabled = enabled
    def show(self): self.visible = True
    def hide(self): self.visible = False
    def setRange(self, *args): pass
    def setValue(self, *args): pass
    def setFormat(self, text): self.text = text


class Worker:
    instances = []
    def __init__(self, mode, info=None, parent=None):
        self.mode, self.info, self.running = mode, info, False
        for key in ("checked", "prepared", "progress", "phase", "failed", "finished"):
            setattr(self, key, Signal())
        self.instances.append(self)
    def start(self): self.running = True
    def isRunning(self): return self.running
    def deleteLater(self): self.deleted = True
    def finish(self):
        self.running = False
        self.finished.emit()


def forbidden(*args, **kwargs):
    pytest.fail("未获用户确认或触发了不应调用的更新路径")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(ui.distribution, "CHANNEL", "user")
    monkeypatch.setattr(ui.custom_update, "enabled", lambda: True)
    monkeypatch.setattr(ui, "CustomUpdateWorker", Worker)
    monkeypatch.setattr(ui.custom_install, "recover", lambda root: {"status": "idle"})
    monkeypatch.setattr(ui.custom_install, "launch_apply", forbidden)
    monkeypatch.setattr(ui.update, "check", forbidden)
    monkeypatch.setattr(ui.patch, "prepare", forbidden)
    monkeypatch.setattr(ui.patch, "launch_apply", forbidden)
    monkeypatch.setattr(ui.patch, "install_root", lambda: tmp_path)
    monkeypatch.setattr(ui, "hard_exit_later", forbidden)
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a: None)
    Worker.instances = []
    saved, quit_calls, prompts = [], [], []
    answer = {"value": ui.QMessageBox.No}
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *a, **k: prompts.append(a[2]) or answer["value"])
    settings = SimpleNamespace(extra={}, save=lambda: saved.append(True))
    w = SimpleNamespace(
        engine=SimpleNamespace(settings=settings, snapshot=lambda: {"phase": "ingame"}),
        _worker=SimpleNamespace(isRunning=lambda: True), _program_startup_checked=False,
        _custom_worker=None, _custom_checked_once=False, _custom_info=None, _custom_ready=None,
        _custom_install_requested=False, _custom_prompt_open=False, _custom_applying=False, _custom_blocked=False,
        _custom_last_success="", custom_update_status=Widget(),
        btn_custom_update=Widget(), custom_update_progress=Widget(), _busy=forbidden,
        tray=SimpleNamespace(quit=lambda: quit_calls.append(True)),
        saved=saved, quit_calls=quit_calls, prompts=prompts, answer=answer, root=tmp_path,
    )
    for name in dir(ui.MainWindow):
        if name.startswith("_custom_") or name in {
            "_check_custom_updates", "_request_custom_update", "_start_custom_download",
            "_apply_custom_ready", "_retire_when_done"}:
            candidate = getattr(ui.MainWindow, name)
            if callable(candidate): setattr(w, name, MethodType(candidate, w))
    return w


def newer():
    return {"status": "newer", "version": "1.1.5.9", "notes": "新版说明", "envelope": b"signed"}


def prepared(window):
    return {"version": "1.1.5.9", "channel": "user", "root": window.root,
            "staging": window.root / "_custom_update/1.1.5.9-user/files",
            "files": {"AramHex.exe": {"sha256": "0" * 64, "size": 1}}}


@pytest.mark.parametrize("mode", ["check", "download"])
def test_maintainer_worker_never_queries_or_downloads_cos(app, monkeypatch, mode):
    monkeypatch.setattr(ui.distribution, "CHANNEL", "maintainer")
    monkeypatch.setattr(ui.custom_update, "enabled", lambda: True)
    monkeypatch.setattr(ui.custom_update, "check", forbidden)
    monkeypatch.setattr(ui.custom_update, "prepare", forbidden)
    worker = ui.CustomUpdateWorker(mode, newer())
    results = []
    worker.checked.connect(results.append)
    worker.run()
    assert results == [{"status": "disabled"}]


def test_custom_worker_uses_only_custom_pipeline(app, monkeypatch):
    monkeypatch.setattr(ui.distribution, "CHANNEL", "user")
    monkeypatch.setattr(ui.custom_update, "enabled", lambda: True)
    monkeypatch.setattr(ui.update, "check", forbidden)
    monkeypatch.setattr(ui.patch, "prepare", forbidden)
    result, got = newer(), []
    monkeypatch.setattr(ui.custom_update, "check", lambda version: result)
    worker = ui.CustomUpdateWorker("check")
    worker.checked.connect(got.append)
    worker.run()
    assert got == [result]


def test_maintainer_custom_entry_points_remain_inert(window, monkeypatch):
    monkeypatch.setattr(ui.distribution, "CHANNEL", "maintainer")
    window._custom_info, window._custom_ready = newer(), prepared(window)
    window._custom_install_requested = True
    window._check_custom_updates(startup=True)
    for action in (window._request_custom_update, window._custom_continue,
                   window._start_custom_download, window._apply_custom_ready):
        action()
    assert not Worker.instances and not window.quit_calls and not window.prompts


def test_startup_check_once_independent_of_data_then_no_runtime_checks(window):
    ui.MainWindow.check_update(window)
    assert len(Worker.instances) == 1 and Worker.instances[0].mode == "check"
    Worker.instances[0].finish()
    for _ in range(3):
        ui.MainWindow.check_update(window)  # 数据定时器/再次打开窗口
        ui.MainWindow.check_update(window, manual=True)
        window._check_custom_updates(manual=True)
        window._check_custom_updates(startup=True)  # 即使重复传入启动标记也无效
    assert len(Worker.instances) == 1


def test_manual_data_check_never_consumes_startup_program_check(window):
    ui.MainWindow.check_update(window, manual=True)
    assert not Worker.instances and not window._program_startup_checked
    ui.MainWindow.check_update(window)
    assert len(Worker.instances) == 1


def test_decline_update_has_no_download_or_install(window):
    window._check_custom_updates(startup=True)
    worker = Worker.instances[-1]
    worker.checked.emit(newer())
    worker.finish()
    window._custom_continue()
    window._start_custom_download()
    window._apply_custom_ready()
    assert len(window.prompts) == 1 and len(Worker.instances) == 1
    assert not window.quit_calls and not window._custom_install_requested
    assert "暂不更新" in window.custom_update_status.text


@pytest.mark.parametrize("version", ["1.1.5.7", "1.1.6", "1.1.6.1"])
@pytest.mark.parametrize("notes", ["", "发布说明不应进入确认弹窗\n" * 50])
def test_confirmation_only_shows_version_question_and_defaults_to_no(window, monkeypatch, version, notes):
    dialogs = []
    def decline(*args):
        dialogs.append(args)
        return ui.QMessageBox.No
    monkeypatch.setattr(ui.QMessageBox, "question", decline)
    window._custom_checked({**newer(), "version": version, "notes": notes})
    assert len(dialogs) == 1
    assert dialogs[0][1:] == ("程序更新", f"发现新版本 {version}，是否现在更新？",
                               ui.QMessageBox.Yes | ui.QMessageBox.No, ui.QMessageBox.No)
    assert not Worker.instances and not window.quit_calls and not window._custom_install_requested


def test_yes_waits_for_check_thread_then_downloads_and_installs_without_game_gate(window, monkeypatch):
    applied = []
    monkeypatch.setattr(ui.custom_install, "launch_apply", lambda *a, **k: applied.append((a, k)))
    window.answer["value"] = ui.QMessageBox.Yes
    window._check_custom_updates(startup=True)
    checker = Worker.instances[-1]
    checker.checked.emit(newer())
    assert window._custom_install_requested and len(Worker.instances) == 1
    checker.finish()
    download = Worker.instances[-1]
    assert len(Worker.instances) == 2 and download.mode == "download"
    assert download.info["envelope"] == b"signed"
    download.prepared.emit(prepared(window))
    assert not applied
    download.finish()
    assert len(applied) == 1 and window.quit_calls == [True]
    assert applied[0][0][4] == "user" and "before_launch" not in applied[0][1]
    window._custom_continue()
    assert len(applied) == 1 and len(window.prompts) == 1


def test_check_finishing_inside_confirmation_dialog_still_starts_download(window, monkeypatch):
    window._check_custom_updates(startup=True)
    checker = Worker.instances[-1]
    def answer_after_nested_event_loop(*args, **kwargs):
        checker.finish()
        return ui.QMessageBox.Yes
    monkeypatch.setattr(ui.QMessageBox, "question", answer_after_nested_event_loop)
    checker.checked.emit(newer())
    assert len(Worker.instances) == 2 and Worker.instances[-1].mode == "download"


def test_check_failure_preserves_success_time_and_never_retries(window):
    window._custom_last_success = "2026-11-06 12:00"
    window._check_custom_updates(startup=True)
    checker = Worker.instances[-1]
    checker.checked.emit({"status": "unavailable", "message": "连接超时"})
    checker.finish()
    for _ in range(3):
        window._custom_continue()
        ui.MainWindow.check_update(window)
        window._request_custom_update()
    assert len(Worker.instances) == 1
    assert "下次启动" in window.custom_update_status.text
    assert "2026-11-06 12:00" in window.custom_update_status.text
    assert not window.prompts and not window.quit_calls


def test_download_failure_requires_new_confirmation_and_reuses_discovered_version(window):
    window.answer["value"] = ui.QMessageBox.Yes
    window._custom_checked(newer())
    download = Worker.instances[-1]
    download.failed.emit("连接超时")
    download.finish()
    window._custom_continue()
    assert len(Worker.instances) == 1 and not window._custom_install_requested
    window._request_custom_update()
    assert len(window.prompts) == 2 and len(Worker.instances) == 2
    assert all(worker.mode == "download" for worker in Worker.instances)
    assert Worker.instances[-1].info["envelope"] == b"signed"


def test_direct_install_without_confirmation_is_inert(window):
    window._custom_info, window._custom_ready = newer(), prepared(window)
    window._start_custom_download()
    window._apply_custom_ready()
    window._custom_continue()
    assert not Worker.instances and not window.quit_calls


def test_install_failure_keeps_old_helper_and_does_not_retry(window, monkeypatch):
    window._custom_info, window._custom_ready = newer(), prepared(window)
    window.answer["value"] = ui.QMessageBox.Yes
    attempts = []
    def fail(*a, **k):
        attempts.append(True)
        raise OSError("文件占用")
    monkeypatch.setattr(ui.custom_install, "launch_apply", fail)
    window._request_custom_update()
    window._custom_continue()
    assert attempts == [True] and not window.quit_calls and window._custom_ready
    assert not window._custom_install_requested and "文件占用" in window.custom_update_status.text


def test_incomplete_install_stays_blocked(window, monkeypatch):
    window._custom_ready = prepared(window)
    window._custom_install_requested = True
    monkeypatch.setattr(ui.custom_install, "recover", lambda root: {"status": "blocked"})
    window._apply_custom_ready()
    assert window._custom_blocked and "保留备份" in window.custom_update_status.text
    assert not window.quit_calls


def test_unpublished_is_not_reported_as_latest(window):
    window._custom_checked({"status": "unpublished"})
    assert "尚未发布" in window.custom_update_status.text and "无需更新" not in window.custom_update_status.text
    assert not window.saved and not window.prompts


def test_wrong_channel_never_becomes_ready(window):
    window._custom_prepared({**prepared(window), "channel": "maintainer"})
    assert window._custom_ready is None and not window.quit_calls


def test_source_checkout_does_not_download_or_install(window, monkeypatch):
    monkeypatch.setattr(ui.patch, "install_root", lambda: None)
    window.answer["value"] = ui.QMessageBox.Yes
    window._custom_checked(newer())
    assert not Worker.instances and "源码运行" in window.custom_update_status.text
    assert not window.quit_calls


def test_last_success_saved_only_for_verified_results(window):
    window._custom_checked({"status": "current", "version": "1.1.5.3"})
    assert window.engine.settings.extra["custom_update_last_success"] == window._custom_last_success
    assert window.saved == [True] and not window.prompts
