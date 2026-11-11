"""构建时选择维护版或用户版；渠道不能由远端云控或用户配置改变。"""
from __future__ import annotations

try:
    # 打包器可单独加入这个小模块，无须修改共享源码。
    from ._distribution import CHANNEL, CUSTOM_PROGRAM_REPO
except ModuleNotFoundError as exc:
    if exc.name != "lolhex._distribution":
        raise
    CHANNEL = "user"
    CUSTOM_PROGRAM_REPO = ""

if CHANNEL not in {"maintainer", "user"}:
    raise ValueError(f"未知构建渠道：{CHANNEL!r}")

UPSTREAM_NOTICES = CHANNEL == "maintainer"
CHANNEL_LABEL = "维护版" if UPSTREAM_NOTICES else "用户版"


def program_update_note() -> str:
    """两个渠道均禁止原版覆盖；用户版仅接收维护者自己的发布。"""
    if UPSTREAM_NOTICES:
        return "维护版：仅在本机维护和安装，原项目新版只提示，交由你评估合并；数据更新照常。"
    return "用户版：程序更新仅来自维护者的发布渠道；数据更新照常，不查询或提示原项目软件版本。"
