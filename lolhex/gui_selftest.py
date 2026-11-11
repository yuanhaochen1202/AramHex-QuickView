"""打包后的离线自检：隔离配置，构造真实启动界面，再检查 OpenCV 和文字识别。

不能只画一个 Qt 小窗口：业务界面导入的依赖也必须来自产物本身。
不使用外部测试库，不启动识别/联网线程，不连接正在运行助手的单例入口。
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


@contextmanager
def _replace_attributes(replacements):
    """仅用于本进程自检的后台任务隔离；无 unittest/pytest 运行依赖。"""
    originals = []
    try:
        for owner, name, value in replacements:
            originals.append((owner, name, name in vars(owner), getattr(owner, name)))
            setattr(owner, name, value)
        yield
    finally:
        for owner, name, own, value in reversed(originals):
            if own:
                setattr(owner, name, value)
            else:
                delattr(owner, name)


@contextmanager
def _isolated_profile():
    """配置和缓存只落入临时目录，退出后恢复调用者环境。"""
    names = ("ARAMHEX_HOME", "APPDATA", "LOCALAPPDATA")
    previous = {name: os.environ.get(name) for name in names}
    try:
        with tempfile.TemporaryDirectory(prefix="aramhex-selftest-") as folder:
            for name in names:
                os.environ[name] = folder
            yield Path(folder)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _startup_check(app, profile: Path) -> dict:
    """真实配置/数据/主窗口/浮层/托盘构造；只禁止外部观察和后台任务。"""
    # 导入必须在这里真实发生，不能用假 MainWindow 掩盖漏打包标准库的错误。
    import getpass  # overlay.run 的单例名称所需；不实际获取名称或连接其他实例。
    import threading
    import requests
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QThread, QTimer, Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from . import cloud, patch
    from .config import Settings, app_home
    from .engine import Engine
    from .ui.mainwindow import MainWindow, Tray
    from .ui.overlay import ControlPanel, Overlay
    from .vision import window as win

    background = []
    event_errors = []
    engine = overlay = main_window = tray = None
    existing_widgets = set(app.topLevelWidgets())
    install_root = profile / "program"
    install_root.mkdir()
    panel_show = ControlPanel.show

    def hidden_panel_show(panel):
        panel.setAttribute(Qt.WA_DontShowOnScreen)
        panel_show(panel)

    def no_network(*args, **kwargs):
        raise RuntimeError("离线启动自检不允许网络请求")

    def python_thread_start(thread, *args, **kwargs):
        background.append("python:" + thread.name)

    def qt_thread_start(thread, *args, **kwargs):
        background.append("qt:" + type(thread).__name__)

    def event_error(kind, value, tb):
        event_errors.append("".join(traceback.format_exception(kind, value, tb)))

    replacements = [
        (threading.Thread, "start", python_thread_start),
        (QThread, "start", qt_thread_start),
        (requests.sessions.Session, "request", no_network),
        (win, "watch_foreground", lambda: None),
        (ControlPanel, "show", hidden_panel_show),
        (patch, "install_root", lambda: install_root),
        (cloud, "_cache", None),
        (sys, "excepthook", event_error),
    ]
    with _replace_attributes(replacements):
        try:
            settings = Settings.load()
            if app_home().resolve() != profile.resolve():
                raise RuntimeError("启动自检配置目录未隔离")
            settings.seen_welcome = True
            if not settings.save():
                raise RuntimeError("启动自检无法保存隔离配置")
            engine = Engine(settings)
            engine.start()  # 保留真实调度逻辑，只阻止工作线程执行。
            overlay = Overlay(engine)
            overlay.timer.stop()  # 不查询游戏窗口，也不触发屏幕识别或截图。
            overlay.set_visible(False)
            main_window = MainWindow(engine, overlay)
            tray = Tray(engine, main_window, overlay)
            main_window.tray = tray
            tray.timer.stop()
            for timer in main_window.findChildren(QTimer):
                timer.stop()
            main_window.setAttribute(Qt.WA_DontShowOnScreen)
            main_window.show()
            main_window.refresh()
            main_window.check_update()  # 真正创建该渠道更新任务，但禁止其线程联网。
            main_window.refresh_rank_if_stale()
            pixmap = QPixmap(main_window.size())
            main_window.render(pixmap)
            loop = QEventLoop()
            QTimer.singleShot(50, loop.quit)
            loop.exec()
            if event_errors:
                raise RuntimeError("启动界面事件异常：\n" + "\n".join(event_errors))
            if pixmap.isNull() or main_window.tabs.count() < 3 or not main_window.status.text():
                raise RuntimeError("真实主窗口未完整构造或渲染")
            if not (profile / "config.json").is_file():
                raise RuntimeError("启动自检没有完成真实配置读写")
            return {"startup_ok": True, "gui_ok": True,
                    "startup_classes": [type(obj).__name__ for obj in (engine, main_window, overlay, tray)],
                    "startup_tabs": main_window.tabs.count(), "isolated_profile": True,
                    "background_tasks_suppressed": background, "startup_event_errors": 0,
                    "single_instance_contacted": False, "external_test_paths_added": False}
        finally:
            if engine is not None:
                engine.stop()
                engine.icons._pool.shutdown(wait=False, cancel_futures=True)
            for owner in (overlay, tray):
                timer = getattr(owner, "timer", None)
                if timer is not None:
                    timer.stop()
                    timer.deleteLater()
            if main_window is not None:
                for timer in main_window.findChildren(QTimer):
                    timer.stop()
            if tray is not None:
                tray.hide()
                tray.deleteLater()
            for widget in set(app.topLevelWidgets()) - existing_widgets:
                widget.hide()
                widget.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def _vision_check() -> dict:
    """OpenCV 的图片处理和 RapidOCR 的检测、识别：画一行字，再认出来。"""
    import cv2
    import numpy as np

    img = np.full((96, 480, 3), 255, np.uint8)
    cv2.putText(img, "AramHex 2026", (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    ok, buf = cv2.imencode(".png", img)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    img = cv2.resize(img, None, fx=1.0, fy=1.0, interpolation=cv2.INTER_AREA)
    from .vision.ocr import OCR
    ocr = OCR.get()
    text, score = ocr.recognize(img)
    lines = ocr.detect(img)
    return {"ocr_text": text, "ocr_score": round(score, 3), "ocr_lines": [ln.text for ln in lines],
            "ocr_ok": bool(ok) and "2026" in text and any("2026" in ln.text for ln in lines)}


def run(report_path: str) -> int:
    report = {"success": False, "startup_ok": False}
    app = None
    owns_app = False
    try:
        with _isolated_profile() as profile:
            from PySide6.QtCore import qVersion
            from PySide6.QtNetwork import QNetworkAccessManager
            from PySide6.QtWidgets import QApplication
            from . import __version__
            app = QApplication.instance()
            owns_app = app is None
            app = app or QApplication([])
            network = QNetworkAccessManager()
            report.update(version=__version__, qt=qVersion(), platform=app.platformName(),
                          network_class=network.metaObject().className())
            report.update(_startup_check(app, profile))
            report.update(_vision_check())
            report["success"] = report["startup_ok"] and report["gui_ok"] and report["ocr_ok"]
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if owns_app and app is not None:
            app.quit()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1
