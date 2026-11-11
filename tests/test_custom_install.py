"""Isolated filesystem/PowerShell tests for the owned-channel installer."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from lolhex import custom_install as install


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path / "userdata"))


def setup_files(tmp_path, extra=None):
    root = tmp_path / "程序 [folder]'s"
    root.mkdir()
    (root / "AramHex.exe").write_bytes(b"old helper")
    stage = root / "_custom_update" / "1.1.5.3-user" / "files"
    stage.mkdir(parents=True)
    blobs = {"AramHex.exe": b"new helper", **(extra or {})}
    files = {}
    for rel, body in blobs.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        files[rel] = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    return root, stage, files


@pytest.mark.parametrize("path", [
    "../AramHex.exe", "_internal/../../bad", "/AramHex.exe", "C:/bad", "_internal\\bad",
    "_internal/x:stream", "_internal//x", "_internal/./x", "_internal/x.", "_internal/x ",
    "_internal/CON", "_internal/com1.txt", "_internal/LPT¹", "_internal/conin$", "_internal/a%20b",
    "config.json", "userdata/config.json", "更新日志.txt", "_custom_update_pending.json", "unins000.exe",
])
def test_reject_unsafe_or_user_files(path):
    with pytest.raises(install.InstallError):
        install.relative_path(path)


def test_expected_program_files_are_allowed():
    for path in ("AramHex.exe", "_internal/Qt6Core.dll", "_internal/a.dist-info/METADATA", "LICENSE", "NOTICE.md", "DATA-LICENSE.txt", "使用说明.txt", "版本说明.txt"):
        assert install.relative_path(path) == path


def test_maintenance_channel_has_no_installer(tmp_path):
    root, stage, files = setup_files(tmp_path)
    with pytest.raises(install.InstallError, match="用户版"):
        install._plan(root, stage, files, "1.1.5.3", "maintainer", None, 99999999)
    assert (root / "AramHex.exe").read_bytes() == b"old helper"


@pytest.mark.parametrize("version", ["1.1.6", "1.1.5.3", "99999.99999.99999.99999"])
def test_release_version_formats_match_manifest(tmp_path, version):
    root, stage, files = setup_files(tmp_path)
    assert install._plan(root, stage, files, version, "user", None, 99999999)["version"] == version


@pytest.mark.parametrize("version", ["1.1", "1.1.5.3.1", "100000.1.2", "1.2.3-beta"])
def test_invalid_release_version_rejected(tmp_path, version):
    root, stage, files = setup_files(tmp_path)
    with pytest.raises(install.InstallError, match="版本号"):
        install._plan(root, stage, files, version, "user", None, 99999999)


def test_damaged_stage_rejected_before_any_transaction(tmp_path):
    root, stage, files = setup_files(tmp_path)
    (stage / "AramHex.exe").write_bytes(b"tampered")
    with pytest.raises(install.InstallError, match="校验"):
        install._plan(root, stage, files, "1.1.5.3", "user", None, 99999999)
    assert not (root / install.PENDING).exists()
    assert not (root / install.BACKUPS).exists()


def test_outside_stage_and_relaunch_rejected(tmp_path):
    root, stage, files = setup_files(tmp_path)
    with pytest.raises(install.InstallError):
        install._plan(root, tmp_path, files, "1.1.5.3", "user", None, 99999999)
    with pytest.raises(install.InstallError, match="重新启动"):
        install._plan(root, stage, files, "1.1.5.3", "user", str(tmp_path / "other.exe"), 99999999)


def test_case_collision_rejected(tmp_path):
    root, stage, files = setup_files(tmp_path, {"_internal/a.dll": b"a"})
    files["_internal/A.dll"] = files["_internal/a.dll"]
    with pytest.raises(install.InstallError, match="大小写"):
        install._plan(root, stage, files, "1.1.5.3", "user", None, 99999999)


def test_space_for_backup_required_before_transaction(tmp_path, monkeypatch):
    root, stage, files = setup_files(tmp_path)
    monkeypatch.setattr(install.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    with pytest.raises(install.InstallError, match="磁盘空间"):
        install._plan(root, stage, files, "1.1.5.3", "user", None, 99999999)
    assert not (root / install.PENDING).exists()


def test_interrupted_transaction_blocks_without_mutation(tmp_path):
    root, stage, files = setup_files(tmp_path)
    pending = root / install.PENDING
    pending.write_text('{"status":"applying"}', encoding="utf-8")
    before = pending.read_bytes()
    assert install.recover(root)["status"] == "blocked"
    with pytest.raises(install.InstallError, match="未完成"):
        install._plan(root, stage, files, "1.1.5.3", "user", None, 99999999)
    assert pending.read_bytes() == before
    assert (root / "AramHex.exe").read_bytes() == b"old helper"


def test_invalid_result_is_blocked_and_kept(tmp_path):
    target = tmp_path / install.RESULT
    target.write_text("partial-json", encoding="utf-8")
    assert install.recover(tmp_path)["status"] == "blocked"
    assert target.read_text(encoding="utf-8") == "partial-json"


def test_result_summary_is_retained_for_ui(tmp_path):
    result = {"status": "success", "version": "1.1.5.3", "channel": "user", "transaction": "abc", "message": "ok"}
    (tmp_path / install.RESULT).write_text(json.dumps(result), encoding="utf-8")
    assert install.recover(tmp_path) == result
    assert install.recover(tmp_path) == result


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_existing_junction_is_rejected(tmp_path):
    root, stage, files = setup_files(tmp_path, {"_internal/test.dll": b"new"})
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = root / "_internal"
    # mklink creates a test junction only; no delete/move command crosses shells.
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(install.InstallError, match="重解析"):
            install._plan(root, stage, files, "1.1.5.3", "user", None, 99999999)
        assert not list(outside.iterdir())
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_real_helper_applies_and_preserves_backup_and_userdata(tmp_path):
    root, stage, files = setup_files(tmp_path, {"_internal/new.dll": b"new dll", "版本说明.txt": "新版本".encode()})
    user = root / "userdata"
    user.mkdir()
    (user / "config.json").write_bytes(b"user settings")
    install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=99999999, wait=True)
    result = install.recover(root)
    assert result["status"] == "success", result
    assert result["channel"] == "user"
    assert (root / "AramHex.exe").read_bytes() == b"new helper"
    assert (root / "_internal/new.dll").read_bytes() == b"new dll"
    assert (user / "config.json").read_bytes() == b"user settings"
    assert (root / result["backup"] / "files/AramHex.exe").read_bytes() == b"old helper"
    assert not (root / install.PENDING).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_timeout_does_not_kill_parent_or_replace_files(tmp_path, monkeypatch):
    root, stage, files = setup_files(tmp_path)
    monkeypatch.setattr(install, "WAIT_SECONDS", 1)
    install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=os.getpid(), wait=True)
    result = install.recover(root)
    assert result["status"] == "cancelled", result
    assert "did not exit" in result["message"]
    assert (root / "AramHex.exe").read_bytes() == b"old helper"
    assert "Stop-Process" not in install._SCRIPT and "taskkill" not in install._SCRIPT


def test_installer_only_checks_designated_helper_pid_and_never_game_processes():
    process_queries = [line.strip() for line in install._SCRIPT.splitlines() if "Get-Process" in line]
    assert process_queries == ["return [bool](Get-Process -Id ([int]$plan.pid) -ErrorAction SilentlyContinue)"]
    assert "Stop-Process" not in install._SCRIPT and "taskkill" not in install._SCRIPT
    assert "LeagueClient" not in install._SCRIPT and "League of Legends" not in install._SCRIPT
    assert "Assert-NoGame" not in install._SCRIPT


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_helper_rechecks_stage_after_python_validation(tmp_path, monkeypatch):
    root, stage, files = setup_files(tmp_path)
    real_popen = install.subprocess.Popen

    def damaged_stage(*args, **kwargs):
        (stage / "AramHex.exe").write_bytes(b"evil bytes")
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(install.subprocess, "Popen", damaged_stage)
    install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=99999999, wait=True)
    result = install.recover(root)
    assert result["status"] == "cancelled", result
    assert (root / "AramHex.exe").read_bytes() == b"old helper"
    assert not (root / install.BACKUPS).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_second_install_is_blocked_while_first_waits(tmp_path, monkeypatch):
    root, stage, files = setup_files(tmp_path)
    monkeypatch.setattr(install, "WAIT_SECONDS", 1)
    handles = []
    real_popen = install.subprocess.Popen

    def track(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        handles.append(proc)
        return proc

    monkeypatch.setattr(install.subprocess, "Popen", track)
    install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=os.getpid())
    try:
        with pytest.raises(install.InstallError, match="未完成|重复"):
            install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=os.getpid())
        assert len(handles) == 1
    finally:
        handles[0].wait(timeout=20)
    assert (root / "AramHex.exe").read_bytes() == b"old helper"


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_partial_failure_rolls_back_earlier_file(tmp_path):
    root, stage, files = setup_files(tmp_path, {"_internal/locked.dll": b"new locked dll"})
    locked = root / "_internal/locked.dll"
    locked.parent.mkdir()
    locked.write_bytes(b"old locked dll")
    # Windows file opened without FILE_SHARE_DELETE cannot be atomically replaced.
    with locked.open("rb"):
        install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=99999999, wait=True)
    result = install.recover(root)
    assert result["status"] == "rolled_back", result
    assert (root / "AramHex.exe").read_bytes() == b"old helper"
    assert locked.read_bytes() == b"old locked dll"
    assert (root / result["backup"] / "files/AramHex.exe").read_bytes() == b"old helper"


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_incomplete_rollback_retains_pending_and_all_backups(tmp_path, monkeypatch):
    root, stage, files = setup_files(tmp_path, {"_internal/locked.dll": b"new locked dll"})
    locked = root / "_internal/locked.dll"
    locked.parent.mkdir()
    locked.write_bytes(b"old locked dll")
    hook = """
        if ($state.status -eq 'applying' -and $target -eq (Join-Path $root 'AramHex.exe')) {
            $script:testLock = [IO.File]::Open($target, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        }
"""
    source = install._SCRIPT.replace("[IO.File]::Replace($source, $target, $oldCopy)", "[IO.File]::Replace($source, $target, $oldCopy)" + hook)
    monkeypatch.setattr(install, "_SCRIPT", source)
    with locked.open("rb"):
        install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=99999999, wait=True)
    assert install.recover(root)["status"] == "blocked"
    pending = json.loads((root / install.PENDING).read_text(encoding="utf-8"))
    assert pending["status"] == "rollback_incomplete"
    assert (root / pending["backup"] / "files/AramHex.exe").read_bytes() == b"old helper"
    with pytest.raises(install.InstallError, match="未完成"):
        install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=99999999)


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_next_success_preserves_previous_backup(tmp_path):
    root, stage, files = setup_files(tmp_path)
    install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=99999999, wait=True)
    previous = install.recover(root)
    assert previous["status"] == "success", previous
    body = b"third helper"
    (stage / "AramHex.exe").write_bytes(body)
    files["AramHex.exe"] = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    install.launch_apply(root, stage, files, "1.1.5.4", "user", pid=99999999, wait=True)
    current = install.recover(root)
    assert current["status"] == "success", current
    assert previous["backup"] != current["backup"]
    assert (root / previous["backup"] / "files/AramHex.exe").read_bytes() == b"old helper"
    assert (root / current["backup"] / "files/AramHex.exe").read_bytes() == b"new helper"


@pytest.mark.skipif(os.name != "nt", reason="Windows helper")
def test_failed_child_launch_releases_own_pending_marker(tmp_path, monkeypatch):
    root, stage, files = setup_files(tmp_path)

    def failure(*args, **kwargs):
        raise OSError("test process creation failure")

    monkeypatch.setattr(install.subprocess, "Popen", failure)
    with pytest.raises(OSError, match="creation failure"):
        install.launch_apply(root, stage, files, "1.1.5.3", "user", pid=99999999)
    assert not (root / install.PENDING).exists()
    assert (root / "AramHex.exe").read_bytes() == b"old helper"


