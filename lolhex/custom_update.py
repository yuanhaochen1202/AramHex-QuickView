"""用户版专用增量更新：签名清单、固定 HTTPS 源、按文件哈希下载。

不读取上游软件云控，不运行网络脚本，不更新用户设置和运行数据。
所有运行库都列在签名清单里，允许首次安装包跨多个版本直接追到新版。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib.parse import urlsplit

import requests

from . import distribution, release_config, update_signature
from .config import app_home, program_dir

log = logging.getLogger(__name__)
MAX_MANIFEST = 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_FILES = 1500
ROOT_FILES = frozenset({"AramHex.exe", "LICENSE", "NOTICE", "NOTICE.md", "PRIVACY.md",
                        "DATA-LICENSE.txt", "使用说明.txt", "版本说明.txt"})
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){2,3}\Z")
_DEVICE = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?\Z", re.I)


class UpdateError(ValueError):
    """安全检查失败或本次更新暂时不可用。"""


def enabled() -> bool:
    return (distribution.CHANNEL == "user"
            and bool(_SHA.fullmatch(release_config.SIGNING_PUBLIC_KEY_HEX)))


def version_key(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise UpdateError("定制版本号格式不正确")
    nums = tuple(map(int, value.split(".")))
    return nums + (0,) * (4 - len(nums))


def relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 220
            or any(c in value for c in "\\:%?#<>\"|*")
            or any(ord(c) < 32 for c in value)):
        raise UpdateError("更新文件路径不合法")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.endswith((" ", ".")) or _DEVICE.fullmatch(p) for p in parts):
        raise UpdateError("更新文件路径不合法")
    if value not in ROOT_FILES and not (len(parts) > 1 and parts[0] == "_internal"):
        raise UpdateError("更新清单包含程序范围外的文件")
    if any(p.casefold() in {"userdata", "screenshots", "__pycache__", ".git"} for p in parts):
        raise UpdateError("更新清单不能包含个人资料")
    return value


def checked_path(root: Path, rel: str = "") -> Path:
    """拒绝任何已存在的重解析点；禁止借 junction/symlink 写到其他目录。"""
    root = Path(os.path.abspath(root))
    target = root / rel
    if not target.resolve().is_relative_to(root.resolve()):
        raise UpdateError("文件路径超出更新目录")
    for item in (target, *target.parents):
        try:
            st = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            raise UpdateError("更新路径包含链接或重解析点")
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("版本清单含重复字段")
        result[key] = value
    return result


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError) as exc:
        raise UpdateError("版本清单格式不正确") from exc


def validate_manifest(manifest: dict, channel: str = "user") -> dict:
    if (not isinstance(manifest, dict) or type(manifest.get("schema")) is not int or manifest["schema"] != 1
            or manifest.get("product") != "aramhex-custom"
            or manifest.get("channel") != channel or channel != "user"):
        raise UpdateError("更新清单不属于这个用户版")
    version_key(manifest.get("version"))
    if not isinstance(manifest.get("notes", ""), str) or len(manifest.get("notes", "")) > 8000:
        raise UpdateError("更新说明过长或不合法")
    files = manifest.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES or "AramHex.exe" not in files:
        raise UpdateError("更新文件清单不完整")
    seen, total, packed_total = set(), 0, 0
    for rel, info in files.items():
        relative_path(rel)
        if rel.casefold() in seen:
            raise UpdateError("更新文件路径大小写冲突")
        seen.add(rel.casefold())
        if not isinstance(info, dict):
            raise UpdateError("更新文件信息不合法")
        for field in ("sha256", "download_sha256"):
            if not isinstance(info.get(field), str) or not _SHA.fullmatch(info[field]):
                raise UpdateError("更新文件缺少有效校验值")
        for field in ("size", "download_size"):
            if type(info.get(field)) is not int or not 0 <= info[field] <= MAX_FILE:
                raise UpdateError("更新文件大小超出允许范围")
        if info["download_size"] < 20:
            raise UpdateError("压缩文件大小不合法")
        total += info["size"]
        packed_total += info["download_size"]
    for rel in seen:
        parts = rel.split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise UpdateError("更新清单存在文件与目录冲突")
    if total > MAX_TOTAL or packed_total > MAX_TOTAL:
        raise UpdateError("更新文件总大小超出允许范围")
    return manifest


def verify_envelope(raw: bytes, public_key: bytes | None = None) -> dict:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_MANIFEST:
        raise UpdateError("版本清单大小不合法")
    data = _json(raw)
    if not isinstance(data, dict) or set(data) != {"payload", "signature"}:
        raise UpdateError("版本清单缺少发布签名")
    try:
        payload = base64.b64decode(data["payload"], validate=True)
        signature = base64.b64decode(data["signature"], validate=True)
        key = public_key if public_key is not None else bytes.fromhex(release_config.SIGNING_PUBLIC_KEY_HEX)
    except (ValueError, TypeError) as exc:
        raise UpdateError("发布签名格式不正确") from exc
    if not update_signature.verify(key, payload, signature):
        raise UpdateError("发布签名验证失败，已拒绝这个更新")
    return validate_manifest(_json(payload))


def _sources() -> tuple[str, ...]:
    result = []
    for base in release_config.UPDATE_BASE_URLS:
        parts = urlsplit(base)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 443) or parts.query or parts.fragment):
            raise UpdateError("内置更新源配置不合法")
        result.append(base.rstrip("/"))
    if not result:
        raise UpdateError("未配置定制版更新源")
    return tuple(result)


def _session():
    session = requests.Session()
    session.trust_env = False  # 国内更新源优先直接连接，不使用环境中的下载代理。
    session.headers.update({"User-Agent": "AramHex-Custom-Updater/1", "Accept-Encoding": "identity"})
    return session


def _read_limited(response, limit: int) -> bytes:
    chunks, size = [], 0
    for chunk in response.iter_content(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise UpdateError("服务器返回的内容超过允许大小")
        chunks.append(chunk)
    return b"".join(chunks)


def _history_path() -> Path:
    return checked_path(app_home(), "custom-update-state.json")


def _reject_older(version: str) -> None:
    path = _history_path()
    try:
        old = _json(path.read_bytes()) if path.exists() else {}
        if not isinstance(old, dict):
            return
        highest = old.get("highest_version", "0.0.0")
        if version_key(version) < version_key(highest):
            raise UpdateError("更新源返回了较旧的版本信息，稍后重试")
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, UpdateError) and "较旧" in str(exc):
            raise


def _remember(version: str) -> None:
    _reject_older(version)
    path = _history_path()
    temporary = checked_path(path.parent, path.name + ".tmp")
    temporary.write_text(json.dumps({"highest_version": version, "last_success": time.time()}), encoding="utf-8")
    temporary.replace(path)


def check(current: str) -> dict:
    if not enabled():
        return {"status": "disabled", "message": "维护版在本机维护，不接收用户版程序更新。"}
    errors, releases, missing = [], [], 0
    try:
        with _session() as session:
            for base in _sources():
                try:
                    with session.get(base + "/channels/user/latest.json", timeout=(5, 12), stream=True,
                                     allow_redirects=False, headers={"Cache-Control": "no-cache"}) as response:
                        if response.status_code == 404:
                            missing += 1
                            continue
                        if 300 <= response.status_code < 400:
                            raise UpdateError("更新源发生重定向，已停止访问")
                        response.raise_for_status()
                        raw = _read_limited(response, MAX_MANIFEST)
                    manifest = verify_envelope(raw)
                    releases.append((manifest, raw))
                except (requests.RequestException, ValueError, OSError, RuntimeError) as exc:
                    errors.append(str(exc))
        if not releases:
            if missing and not errors:
                return {"status": "unpublished", "message": "用户版更新尚未发布；当前程序仍可正常使用。"}
            return {"status": "unavailable", "message": "暂时无法检查用户版更新，请稍后重试。"}
        manifest, raw = max(releases, key=lambda item: version_key(item[0]["version"]))
        _remember(manifest["version"])
        return {"status": "newer" if version_key(manifest["version"]) > version_key(current) else "current",
                "version": manifest["version"], "notes": manifest.get("notes", ""), "envelope": raw}
    except (ValueError, OSError, RuntimeError) as exc:
        errors.append(str(exc))
        return {"status": "unavailable", "message": str(exc)}
    finally:
        if errors:
            log.info("用户版更新检查未完成：%s", "; ".join(errors))


def _valid(path: Path, size: int, sha: str) -> bool:
    return path.is_file() and path.stat().st_size == size and sha256_file(path) == sha


def plan(manifest: dict, root: Path) -> dict:
    validate_manifest(manifest)
    changed = {}
    for rel, info in manifest["files"].items():
        path = checked_path(root, rel)
        if not _valid(path, info["size"], info["sha256"]):
            changed[rel] = info
    return changed


def _download_blob(info: dict, folder: Path, session, sources: tuple[str, ...], progress=None) -> Path:
    sha, total = info["download_sha256"], info["download_size"]
    folder = checked_path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    ready = checked_path(folder, sha + ".gz")
    partial = checked_path(folder, sha + ".part")
    if _valid(ready, total, sha):
        if progress:
            progress(total)
        return ready
    if ready.exists():
        ready.unlink()
    last = None
    for base in sources:
        try:
            start = partial.stat().st_size if partial.is_file() else 0
            if start > total:
                partial.unlink()
                start = 0
            if start == total:
                if _valid(partial, total, sha):
                    partial.replace(ready)
                    if progress:
                        progress(total)
                    return ready
                partial.unlink()
                start = 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            with session.get(base + "/objects/" + sha + ".gz", headers=headers,
                             stream=True, timeout=(8, 20), allow_redirects=False) as response:
                if response.status_code == 416:
                    partial.unlink(missing_ok=True)
                    raise UpdateError("续传位置已失效，下次将重新下载这个文件")
                response.raise_for_status()
                if response.status_code not in (200, 206):
                    raise UpdateError("更新文件响应不合法")
                if response.status_code == 206:
                    value = response.headers.get("Content-Range", "")
                    if value != f"bytes {start}-{total - 1}/{total}":
                        raise UpdateError("服务器返回的续传范围不正确")
                else:
                    start = 0  # 服务端忽略 Range，安全地从头写，不能把整份数据追加进去。
                downloaded = start
                with partial.open("ab" if start else "wb") as output:
                    for chunk in response.iter_content(128 * 1024):
                        downloaded += len(chunk)
                        if downloaded > total:
                            raise UpdateError("下载文件超过签名清单的大小")
                        output.write(chunk)
                        if progress:
                            progress(downloaded)
            if not _valid(partial, total, sha):
                if partial.is_file() and partial.stat().st_size >= total:
                    partial.unlink()
                raise UpdateError("下载文件校验失败或尚未下载完整")
            partial.replace(ready)
            return ready
        except (requests.RequestException, OSError, ValueError) as exc:
            last = exc
    raise UpdateError(f"更新文件下载未完成，可稍后续传：{last}")


def _unpack(blob: Path, destination: Path, info: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = checked_path(destination.parent, destination.name + ".partial")
    try:
        size, digest = 0, hashlib.sha256()
        with gzip.open(blob, "rb") as source, temporary.open("wb") as target:
            while chunk := source.read(128 * 1024):
                size += len(chunk)
                if size > info["size"]:
                    raise UpdateError("解压文件超过签名清单的大小")
                digest.update(chunk)
                target.write(chunk)
        if size != info["size"] or digest.hexdigest() != info["sha256"]:
            raise UpdateError("解压文件校验失败")
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def prepare(info: dict, progress=None, phase=None, root: Path | None = None) -> dict:
    if not enabled():
        raise UpdateError("这个维护版不接收用户版程序更新")
    from . import __version__, custom_install
    manifest = verify_envelope(info.get("envelope"))
    _reject_older(manifest["version"])
    if version_key(manifest["version"]) <= version_key(__version__):
        raise UpdateError("这个更新版本不高于当前版本")
    if root is None and not getattr(sys, "frozen", False):
        raise UpdateError("源码运行时不安装程序更新")
    root = checked_path(Path(root) if root is not None else program_dir())
    if custom_install.recover(root).get("status") == "blocked":
        raise UpdateError("上次更新尚未完整恢复，已保留备份；请先手动恢复程序")
    say = phase or (lambda _: None)
    say("比对本机文件…")
    changed = plan(manifest, root)
    staging = checked_path(root, f"_custom_update/{manifest['version']}-user/files")
    staging.mkdir(parents=True, exist_ok=True)
    cache = checked_path(app_home(), "custom-update-objects")
    total, done = sum(f["download_size"] for f in changed.values()), 0
    sources = _sources()
    with _session() as session:
        for rel, spec in changed.items():
            destination = checked_path(staging, rel)
            if _valid(destination, spec["size"], spec["sha256"]):
                done += spec["download_size"]
                if progress:
                    progress(done, total)
                continue
            say(f"下载更新文件（{rel}）…")
            def report(count):
                if progress:
                    progress(done + count, total)
            blob = _download_blob(spec, cache, session, sources, report)
            _unpack(blob, destination, spec)
            blob.unlink(missing_ok=True)  # 已验证的原文件已暂存，不保留重复压缩缓存。
            done += spec["download_size"]
    if "AramHex.exe" not in changed:
        # 一个新的软件版本必须带有它的可执行文件；拒绝误发只改数据的“程序新版”。
        raise UpdateError("新版主程序与本机相同，请维护者检查发布清单")
    return {"root": root, "staging": staging, "files": changed,
            "version": manifest["version"], "channel": "user"}
