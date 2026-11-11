"""只读提示原项目新版，绝不返回安装包、下载程序或调用程序更新器。"""
from __future__ import annotations

import logging
import re

import requests

from . import distribution


UPSTREAM_VERSION = "1.1.5"  # 合并上游代码后才提高；独立于本地版的软件版本号。
UPSTREAM_REPO = "czgreat/aramhex"
RELEASES_PAGE = f"https://github.com/{UPSTREAM_REPO}/releases"
SOURCES = (
    "https://aramhex.com/u/latest.json",
    "https://aramhex.cn/u/latest.json",
    f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest",
)
_VERSION = re.compile(r"v?(\d+(?:\.\d+){1,3})", re.I)
log = logging.getLogger(__name__)


def _version(value) -> tuple[str, tuple[int, ...]]:
    match = _VERSION.fullmatch(str(value).strip())
    if not match:
        raise ValueError("原项目版本号格式不正确")
    version = match.group(1)
    numbers = tuple(int(n) for n in version.split("."))
    return version, numbers + (0,) * (4 - len(numbers))


def check(timeout: float = 5) -> dict:
    """优先国内元数据；只读取正式版本号，忽略下载地址、脚本和云控升级规则。"""
    if not distribution.UPSTREAM_NOTICES:
        return {"status": "disabled"}
    baseline = _version(UPSTREAM_VERSION)[1]
    for url in SOURCES:
        try:
            response = requests.get(url, timeout=timeout,
                                    headers={"User-Agent": "aramhex-custom-upstream-check",
                                             "Accept": "application/json"})
            response.raise_for_status()
            info = response.json()
            if not isinstance(info, dict) or info.get("draft") or info.get("prerelease"):
                raise ValueError("没有可用的原项目正式版本信息")
            version, key = _version(info.get("version") or info.get("tag_name") or "")
            return {"status": "newer" if key > baseline else "current", "version": version}
        except (requests.RequestException, ValueError, TypeError) as exc:
            log.info("原项目版本信息暂不可用（%s）：%s", url, exc)
    return {"status": "unavailable"}


def message(info: dict) -> str:
    if info.get("status") == "newer":
        return f"原项目有新版 {info['version']}，可交给 GPT 评估合并；本地版不会自动替换。"
    if info.get("status") == "current":
        return f"原项目暂未发现比基础版本 {UPSTREAM_VERSION} 更新的正式版本。"
    return "原项目版本信息暂时未查到，不影响数据更新和本地识别。"
