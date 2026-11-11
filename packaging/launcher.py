"""打包后的程序入口（PyInstaller）。

启动失败时写入 crash.log 并给出定制渠道的处理说明。
渠道和可选恢复地址由构建器直接写入，不依赖已经可能导入失败的应用模块。
"""

import os
import sys
import time
import traceback

# 用户版的版本化恢复地址由 launcher_config 在打包时写入。
BUILD_CHANNEL = "user"
USER_RECOVERY_URL = ""


def _crash_log(text: str) -> str:
    base = os.environ.get("ARAMHEX_HOME")
    if not base and os.path.isfile(os.path.join(os.path.dirname(sys.executable), "portable.txt")):
        base = os.path.join(os.path.dirname(sys.executable), "userdata")
    if not base:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AramHex")
    try:
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "crash.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}\n")
        return path
    except OSError:
        return ""


def _crash(text: str) -> None:
    path = _crash_log(text)
    recovery_url = USER_RECOVERY_URL if BUILD_CHANNEL == "user" else ""
    if BUILD_CHANNEL == "user":
        msg = ("海克斯大乱斗助手（用户版）启动失败。\n\n"
               "请保留错误记录并联系维护者排查。")
        if recovery_url:
            msg += ("\n\n需要重新下载时，可使用维护者提供的用户版完整包。"
                    "请先解压到新文件夹，保留原来的 userdata。\n"
                    "点「是」打开下载链接，点「否」关闭。助手不会自动安装程序。")
    else:
        msg = ("海克斯大乱斗助手（维护版）启动失败。\n\n"
               "这是本机维护版本，请保留错误记录，在维护聊天中排查并修复。"
               "此窗口不会下载或安装其他版本。")
    if path:
        msg += f"\n\n错误记录：{path}"
    try:
        import ctypes
        # 用户版：是/否，默认选否；维护版及无恢复地址时只显示确定。
        buttons = 0x4 | 0x100 if recovery_url else 0
        answer = ctypes.windll.user32.MessageBoxW(None, msg, "海克斯大乱斗助手", buttons | 0x10)
        if recovery_url and answer == 6:
            import webbrowser
            webbrowser.open(recovery_url)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from lolhex.__main__ import main
        code = main()
    except SystemExit:
        raise
    except BaseException:
        _crash(traceback.format_exc())
        code = 1
    sys.exit(code)
