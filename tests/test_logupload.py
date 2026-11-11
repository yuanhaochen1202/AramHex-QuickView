"""匿名改善功能已移除：旧配置、旧调用及原始云指令都不能恢复日志外传。"""
import ast
import builtins
import copy
import logging
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import requests

from lolhex import cloud, logupload

LEGACY_ID = "0123456789abcdef"


def raw_upload_request():
    return {"logs": {"url": "https://aramhex.com/api/logs", "request": [LEGACY_ID, LEGACY_ID + ":retry"],
                     "verbose": [LEGACY_ID + ":debug"], "max_kb": 4096}}


@pytest.mark.parametrize("filtered", [False, True])
@pytest.mark.parametrize("debug_mode", [False, True])
def test_legacy_requests_never_read_logs_send_network_or_schedule_work(tmp_path, monkeypatch, filtered, debug_mode):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    text = b"private local log sentinel\n"
    log_path = tmp_path / "aramhex.log"
    log_path.write_bytes(text)
    (tmp_path / "aramhex.log.1").write_bytes(text)
    settings = SimpleNamespace(install_id=LEGACY_ID, send_usage=True, debug_mode=debug_mode,
                               extra={"logs_sent": [], "logs_tries": {}, "verbose_seen": {}})
    before = copy.deepcopy(vars(settings))
    cfg = cloud._merge(raw_upload_request()) if filtered else raw_upload_request()
    cfg_before = copy.deepcopy(cfg)
    root = logging.getLogger()
    level = root.level

    def forbidden(*args, **kwargs):
        raise AssertionError("removed log upload must not perform IO, logging changes or background work")

    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", forbidden)
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(requests.sessions.Session, "request", forbidden)
        guard.setattr(requests, "get", forbidden)
        guard.setattr(requests, "post", forbidden)
        guard.setattr(threading.Thread, "start", forbidden)
        guard.setattr(logging.Logger, "setLevel", forbidden)
        for now in (0, 1000, 1000 + 25 * 3600):
            assert logupload.maybe_upload(settings, cfg) == []
            assert logupload.apply_verbose(settings, cfg, now=now) is debug_mode

    assert vars(settings) == before and cfg == cfg_before
    assert root.level == level and log_path.read_bytes() == text


def test_upload_compatibility_stub_does_not_even_inspect_settings_or_cfg():
    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("disabled upload must not inspect old settings or requests")

    assert logupload.maybe_upload(Unreadable(), Unreadable()) == []


@pytest.mark.parametrize("debug_mode", [False, True])
def test_verbose_compatibility_only_reports_local_debug_flag(debug_mode, monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.WARNING)
    settings = SimpleNamespace(debug_mode=debug_mode)
    assert logupload.apply_verbose(settings, raw_upload_request(), now=1e9) is debug_mode
    assert root.level == logging.WARNING
    assert vars(settings) == {"debug_mode": debug_mode}


def test_logupload_shim_has_no_imported_transport_file_or_timer_dependencies():
    tree = ast.parse(Path(logupload.__file__).read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
