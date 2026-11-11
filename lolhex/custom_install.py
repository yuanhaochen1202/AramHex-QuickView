"""Apply an already verified user release after the helper has exited.

This module never downloads code.  A fixed local PowerShell helper verifies
staged files again, preserves originals, and refuses unfinished transactions.
``recover`` is deliberately read-only: an interrupted installation must not
silently overwrite the only remaining recovery copy.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
from pathlib import Path

PENDING = "_custom_update_pending.json"
RESULT = "_custom_update_result.json"
BACKUPS = "_custom_update_backup"
WAIT_SECONDS = 30
MAX_FILE_SIZE = 512 * 1024 * 1024
ROOT_FILES = frozenset({"AramHex.exe", "LICENSE", "NOTICE", "NOTICE.md", "PRIVACY.md", "DATA-LICENSE.txt", "使用说明.txt", "版本说明.txt"})
_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]|conin\$|conout\$)(?:\.|$)", re.I)


class InstallError(ValueError):
    """The update cannot safely be installed in this directory."""


def relative_path(value: str) -> str:
    """Only canonical, non-device Windows paths within our program files."""
    if not isinstance(value, str) or not value or len(value) > 240:
        raise InstallError("更新文件路径不合法")
    if any(c in value for c in '\\:?#<>"|*%') or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InstallError("更新文件路径不合法")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.endswith((".", " ")) or _DEVICE.match(p) for p in parts):
        raise InstallError("更新文件路径不合法")
    if value not in ROOT_FILES and not (parts[0] == "_internal" and len(parts) > 1):
        raise InstallError("更新清单包含程序范围以外的文件")
    return value


def _safe_path(path: Path, *, directory: bool | None = None) -> Path:
    """Reject links/junctions in every existing ancestor, including the root."""
    path = Path(os.path.abspath(path))
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InstallError("更新目录包含链接或重解析点，不能自动替换")
    if path.exists() and directory is not None and path.is_dir() != directory:
        raise InstallError("更新路径的文件类型不正确")
    return path


def _child(root: Path, rel: str, *, directory: bool | None = None) -> Path:
    target = _safe_path(root / rel, directory=directory)
    if not target.is_relative_to(root) or target == root:
        raise InstallError("更新文件越过程序目录")
    return target


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recover(root: Path) -> dict:
    """Read startup status without repairing, deleting, or executing anything.

    ``blocked`` means a transaction was interrupted (or its record is damaged).
    The caller must disable further installs and leave the backups untouched.
    Result records are retained, so the UI may deduplicate using transaction.
    """
    try:
        root = _safe_path(Path(root), directory=True)
        pending = _child(root, PENDING, directory=False)
        if pending.exists():
            return {"status": "blocked", "message": "检测到未完成的程序更新，已保留备份；请联系维护者恢复后再更新。"}
        result = _child(root, RESULT, directory=False)
        if not result.exists():
            return {"status": "idle"}
        if result.stat().st_size > 64 * 1024:
            raise InstallError("更新结果记录过大")
        info = json.loads(result.read_text(encoding="utf-8-sig"))
        if not isinstance(info, dict) or info.get("status") not in {"success", "rolled_back", "cancelled"}:
            raise InstallError("更新结果记录不完整")
        return {key: info[key] for key in ("status", "version", "channel", "transaction", "message", "backup") if key in info}
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "blocked", "message": f"无法确认上次程序更新是否完整，已停止自动更新：{exc}"}


def _plan(root: Path, staging: Path, files: dict, version: str, channel: str,
          relaunch: str | None, pid: int) -> dict:
    if channel != "user":
        raise InstallError("只有用户版可以安装自有渠道更新")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){2,3}", version):
        raise InstallError("更新版本号不正确")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < 2**32:
        raise InstallError("等待退出的程序编号不正确")
    root = _safe_path(Path(root), directory=True)
    if not root.is_dir() or root.parent == root or not _child(root, "AramHex.exe", directory=False).is_file():
        raise InstallError("没有找到已安装的 AramHex.exe")
    staging = _safe_path(Path(staging), directory=True)
    if not staging.is_dir() or not staging.is_relative_to(root):
        raise InstallError("暂存目录不在程序目录内")
    staging_rel = staging.relative_to(root).as_posix()
    if not staging_rel.startswith("_custom_update/") or len(staging.relative_to(root).parts) < 3:
        raise InstallError("暂存目录必须位于专用更新目录内")
    if not isinstance(files, dict) or not files or len(files) > 1500:
        raise InstallError("更新文件清单为空或过大")
    clean, folded = [], set()
    for rel, item in files.items():
        relative_path(rel)
        if rel.casefold() in folded:
            raise InstallError("更新文件路径大小写冲突")
        folded.add(rel.casefold())
        if not isinstance(item, dict):
            raise InstallError("更新文件校验信息不正确")
        size, digest = item.get("size"), item.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_SIZE:
            raise InstallError("更新文件大小不正确")
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise InstallError("更新文件 SHA256 不正确")
        source = _child(staging, rel, directory=False)
        _child(root, rel, directory=False)
        if not source.is_file() or source.stat().st_size != size or _sha(source) != digest:
            raise InstallError(f"暂存文件校验失败：{rel}")
        clean.append({"path": rel, "size": size, "sha256": digest})
    if sum(item["size"] for item in clean) > 1024**3:
        raise InstallError("更新总大小超限")
    old_bytes = sum((root / item["path"]).stat().st_size for item in clean if (root / item["path"]).is_file())
    required_free = 2 * old_bytes + 2 * sum(item["size"] for item in clean) + 64 * 1024 * 1024
    if shutil.disk_usage(root).free < required_free:
        raise InstallError("剩余磁盘空间不足以同时保留更新文件和回滚备份")
    # A file and a directory cannot share a path even if all leaf paths are legal.
    for rel in folded:
        if any(parent in folded for parent in ("/".join(rel.split("/")[:i]) for i in range(1, len(rel.split("/"))))):
            raise InstallError("更新清单存在文件和目录冲突")
    if relaunch is not None:
        target = _safe_path(Path(relaunch), directory=False)
        if target != root / "AramHex.exe":
            raise InstallError("更新后只能重新启动本目录的助手")
    if recover(root).get("status") == "blocked":
        raise InstallError("上一次更新未完成，已停止安装并保留备份")
    transaction = secrets.token_hex(16)
    _child(root, "_custom_update", directory=True)
    _child(root, BACKUPS, directory=True)
    _child(root, PENDING, directory=False)
    _child(root, RESULT, directory=False)
    return {"schema": 1, "transaction": transaction, "status": "prepared", "root": str(root),
            "staging": str(staging), "backup": f"{BACKUPS}/{version}-{transaction}",
            "files": clean, "version": version, "channel": channel, "pid": pid,
            "relaunch": bool(relaunch), "wait_seconds": WAIT_SECONDS, "required_free": required_free}


_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$plan = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PLAN__')) | ConvertFrom-Json
$root = [IO.Path]::GetFullPath([string]$plan.root).TrimEnd('\')
$stage = [IO.Path]::GetFullPath([string]$plan.staging).TrimEnd('\')
$pending = Join-Path $root '_custom_update_pending.json'
$result = Join-Path $root '_custom_update_result.json'
$backup = Join-Path $root ([string]$plan.backup)
$entries = @()
$mutated = $false
$backupReady = $false
$parentExited = $false
$completed = $false
$ownsTransaction = $false

function Assert-Safe([string]$path, [string]$base = $root) {
    $full = [IO.Path]::GetFullPath($path)
    $prefix = [IO.Path]::GetFullPath($base).TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Path outside program directory' }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $attributes = [IO.File]::GetAttributes($cursor)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point in update path' }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}
function Assert-File([string]$path, [long]$size, [string]$digest) {
    $null = Assert-Safe $path
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or $item.Length -ne $size -or (File-Sha $path) -ine $digest) { throw 'File verification failed' }
}
function File-Sha([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose(); $stream.Dispose() }
}
function Save-Json([string]$path, $value) {
    $null = Assert-Safe $path
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($value | ConvertTo-Json -Depth 10 -Compress))
    $stream = [IO.File]::Open($path, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function Save-State([string]$status, [string]$message) {
    $script:state = [ordered]@{schema=1; transaction=$plan.transaction; status=$status; version=$plan.version; channel=$plan.channel; backup=$plan.backup; message=$message; entries=@($script:entries)}
    Save-Json $pending $script:state
    if ($script:backupReady) { Save-Json (Join-Path $backup 'transaction.json') $script:state }
}
function Save-Result {
    Save-Json $result ([ordered]@{schema=1; transaction=$plan.transaction; status=$state.status; version=$plan.version; channel=$plan.channel; backup=$plan.backup; message=$state.message})
}
function Parent-Alive {
    return [bool](Get-Process -Id ([int]$plan.pid) -ErrorAction SilentlyContinue)
}
function Ensure-Parent([string]$path) {
    $null = Assert-Safe $path
    $directory = Split-Path -Parent $path
    if ([IO.Path]::GetFullPath($directory).TrimEnd('\') -ine $root) { $null = Assert-Safe $directory }
    if (-not (Test-Path -LiteralPath $directory)) { $null = [IO.Directory]::CreateDirectory($directory) }
    $null = Assert-Safe $path
}
function Place-File([string]$source, [string]$target, [string]$oldCopy) {
    $null = Assert-Safe $source
    $null = Assert-Safe $target
    if (Test-Path -LiteralPath $target) {
        Ensure-Parent $oldCopy
        [IO.File]::Replace($source, $target, $oldCopy)
    } else {
        [IO.File]::Move($source, $target)
    }
}

try {
    $null = Assert-Safe $pending
    $marker = Get-Content -LiteralPath $pending -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($marker.transaction -ne $plan.transaction -or $marker.status -ne 'prepared') { throw 'Another update transaction is active' }
    $ownsTransaction = $true
    if ($plan.channel -ne 'user') { throw 'Wrong update channel' }
    $null = Assert-Safe $stage
    $null = Assert-Safe $backup
    if (Test-Path -LiteralPath $backup) { throw 'Backup destination already exists' }
    $deadline = [DateTime]::UtcNow.AddSeconds([int]$plan.wait_seconds)
    while (Parent-Alive) {
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Helper did not exit; no files were replaced' }
        Start-Sleep -Milliseconds 200
    }
    $parentExited = $true
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
    if ($drive.AvailableFreeSpace -lt [long]$plan.required_free) { throw 'Insufficient free space for rollback-safe update' }
    foreach ($file in @($plan.files)) {
        $src = Join-Path $stage ([string]$file.path)
        $dst = Join-Path $root ([string]$file.path)
        Assert-File $src ([long]$file.size) ([string]$file.sha256)
        $null = Assert-Safe $dst
        if ((Test-Path -LiteralPath $dst) -and (Get-Item -LiteralPath $dst -Force).PSIsContainer) { throw 'Destination is a directory' }
    }
    $null = [IO.Directory]::CreateDirectory($backup)
    $backupReady = $true
    foreach ($file in @($plan.files)) {
        $dst = Join-Path $root ([string]$file.path)
        $exists = Test-Path -LiteralPath $dst
        $oldSize = 0L
        $oldHash = ''
        if ($exists) {
            $oldSize = (Get-Item -LiteralPath $dst -Force).Length
            $oldHash = File-Sha $dst
        }
        $entries += [ordered]@{path=[string]$file.path; original=[bool]$exists; oldSize=$oldSize; oldSha256=$oldHash; size=[long]$file.size; sha256=[string]$file.sha256}
    }
    Save-State 'backing_up' 'Saving original program files'
    foreach ($entry in $entries) {
        if ($entry.original) {
            $dst = Join-Path $root $entry.path
            $bak = Join-Path (Join-Path $backup 'files') $entry.path
            Assert-File $dst $entry.oldSize $entry.oldSha256
            Ensure-Parent $bak
            Copy-Item -LiteralPath $dst -Destination $bak
            Assert-File $bak $entry.oldSize $entry.oldSha256
        }
    }
    Save-State 'applying' 'Replacing verified program files'
    foreach ($entry in $entries) {
        $src = Join-Path $stage $entry.path
        $dst = Join-Path $root $entry.path
        $candidate = Join-Path (Join-Path $backup 'candidates') $entry.path
        Assert-File $src $entry.size $entry.sha256
        $null = Assert-Safe $dst
        if ($entry.original) { Assert-File $dst $entry.oldSize $entry.oldSha256 }
        elseif (Test-Path -LiteralPath $dst) { throw 'New destination appeared after planning' }
        Ensure-Parent $candidate
        Copy-Item -LiteralPath $src -Destination $candidate
        Assert-File $candidate $entry.size $entry.sha256
        Ensure-Parent $dst
        $mutated = $true
        Place-File $candidate $dst (Join-Path (Join-Path $backup 'replaced') $entry.path)
        Assert-File $dst $entry.size $entry.sha256
    }
    foreach ($entry in $entries) { Assert-File (Join-Path $root $entry.path) $entry.size $entry.sha256 }
    Save-State 'success' 'Update installed; original files retained in backup'
    Save-Result
    $null = Assert-Safe $pending
    Remove-Item -LiteralPath $pending
    $completed = $true
} catch {
    if (-not $ownsTransaction) { exit 2 }
    $reason = $_.Exception.Message
    $restored = $true
    if ($mutated) {
        try { Save-State 'rolling_back' $reason } catch { $restored = $false }
        foreach ($entry in $entries) {
            try {
                $dst = Join-Path $root $entry.path
                $null = Assert-Safe $dst
                if ($entry.original) {
                    $bak = Join-Path (Join-Path $backup 'files') $entry.path
                    Assert-File $bak $entry.oldSize $entry.oldSha256
                    # The unchanged original needs no write, including when it is locked.
                    $same = $false
                    try { Assert-File $dst $entry.oldSize $entry.oldSha256; $same = $true } catch { }
                    if (-not $same) {
                        $candidate = Join-Path (Join-Path $backup 'restore') $entry.path
                        Ensure-Parent $candidate
                        Copy-Item -LiteralPath $bak -Destination $candidate -Force
                        Assert-File $candidate $entry.oldSize $entry.oldSha256
                        Ensure-Parent $dst
                        Place-File $candidate $dst (Join-Path (Join-Path $backup 'failed-new') $entry.path)
                        Assert-File $dst $entry.oldSize $entry.oldSha256
                    }
                } elseif (Test-Path -LiteralPath $dst) {
                    # Never delete a file which appeared independently during the update.
                    Assert-File $dst $entry.size $entry.sha256
                    Remove-Item -LiteralPath $dst
                }
            } catch { $restored = $false }
        }
    }
    if ($restored) {
        $status = if ($mutated) { 'rolled_back' } else { 'cancelled' }
        try {
            Save-State $status $reason
            Save-Result
            $null = Assert-Safe $pending
            $marker = Get-Content -LiteralPath $pending -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($marker.transaction -ne $plan.transaction) { throw 'Transaction ownership changed' }
            Remove-Item -LiteralPath $pending
            $completed = $true
        } catch { $completed = $false }
    } else {
        try { Save-State 'rollback_incomplete' $reason; Save-Result } catch { }
    }
}
if ($completed -and $plan.relaunch -and $parentExited -and -not (Parent-Alive)) {
    try {
        $exe = Assert-Safe (Join-Path $root 'AramHex.exe')
        Start-Process -FilePath $exe -WorkingDirectory $root -WindowStyle Hidden
    } catch {
        # Installation result remains available for the next manual launch.
    }
}
if (-not $completed) { exit 2 }
'''


def apply_script(plan: dict) -> str:
    """Serialize data into a fixed helper; no remote content becomes script text."""
    payload = base64.b64encode(json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return _SCRIPT.replace("__PLAN__", payload)


def launch_apply(root: Path, staging: Path, files: dict[str, dict], version: str,
                 channel: str, relaunch: str | None = None, pid: int | None = None,
                 wait: bool = False) -> Path:
    """Start the local installer, then let the caller exit normally.

    No installation occurs in this Python process. The exclusive pending marker
    also prevents two invocations from applying the same version concurrently.
    ``wait`` is only for isolated tests and never terminates a timed-out helper.
    The caller obtains the user's update consent before invoking this function.
    Only the specified helper PID is checked; game/client processes are untouched.
    """
    if os.name != "nt":
        raise InstallError("自动安装仅支持 Windows")
    plan = _plan(root, staging, files, version, channel, relaunch, os.getpid() if pid is None else pid)
    root = Path(plan["root"])
    pending = root / PENDING
    script_dir = _child(root, "_custom_update", directory=True)
    script = _child(root, f"_custom_update/apply-{plan['transaction']}.ps1", directory=False)
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        raise InstallError("没有找到 Windows PowerShell")
    script_dir.mkdir(exist_ok=True)
    # Exclusive creation is the lock. A crash leaves it in place and blocks the
    # next updater instead of allowing a second transaction to consume backups.
    try:
        with pending.open("x", encoding="utf-8") as output:
            json.dump(plan, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise InstallError("已有程序更新事务，不能重复安装") from exc
    try:
        with script.open("x", encoding="utf-8-sig") as output:
            output.write(apply_script(plan))
        args = [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(args, cwd=str(root), creationflags=flags, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except BaseException:
        # No child was started: do not leave a false unfinished transaction.
        if json.loads(pending.read_text(encoding="utf-8")).get("transaction") == plan["transaction"]:
            pending.unlink()
        raise
    if wait:
        proc.wait(timeout=180)
    return script
