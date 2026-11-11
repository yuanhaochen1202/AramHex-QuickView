"""真实主窗口拒绝上游设置覆盖，保留查分资料更新；无界面模式运行。"""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def mw(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from lolhex import cloud
    from lolhex.config import Settings
    from lolhex.engine import Engine
    from lolhex.ui import mainwindow, overlay
    monkeypatch.setattr(mainwindow.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    s = Settings.load()
    eng = Engine(s)
    w = mainwindow.MainWindow(eng, overlay.Overlay(eng))
    toasts = []
    w._toast = toasts.append
    w.toasts = toasts
    yield w, s, eng, cloud, tmp_path
    w.close()
    app.processEvents()


def test_raw_cloud_rules_cannot_override_settings_or_player_changes(mw, monkeypatch):
    w, s, _, cloud, home = mw
    # 直接交给 UI 未过滤的旧缓存/排队信号，UI 自己也必须执行白名单。
    cfg = {"settings": [{"id": "t1", "defaults": {"speed": "saver"},
                          "force": {"badge_position": "bottom"}, "reason": "测试原因"}]}
    monkeypatch.setattr(cloud, "get", lambda: cfg)
    w._apply_cloud_settings(cfg)
    w._apply_cloud_settings(cfg)
    assert w.toasts == [] and not s.extra.get("seen_force")
    assert s.speed == "standard" and w.set_speed.currentData() == "standard"
    assert s.badge_position == "top" and w.set_badge.currentData() == "top" and s.forced == {}
    assert "speed" not in s.user_set  # 没手选过的默认值也不能被上游更改。
    w.set_badge.setCurrentIndex(w.set_badge.findData("bottom"))
    w._save_settings()
    assert s.badge_position == "bottom" and s.forced == {} and "badge_position" in s.user_set
    cfg["settings"][0]["force"]["badge_position"] = "top"
    w._apply_cloud_settings(cfg)
    assert s.badge_position == "bottom" and w.set_badge.currentData() == "bottom" and s.forced == {}
    assert json.loads((home / "config.json").read_text(encoding="utf-8"))["badge_position"] == "bottom"
    empty = cloud._merge({})
    monkeypatch.setattr(cloud, "get", lambda: empty)
    w._apply_cloud_settings(empty)
    assert s.badge_position == "bottom" and w.set_badge.currentData() == "bottom" and s.speed == "standard"
    assert w.toasts == []


def test_cloud_filter_restores_old_forced_setting_in_ui_and_storage(mw):
    w, s, _, _, home = mw
    s.badge_position = "top"
    s.forced = {"badge_position": "bottom"}
    w._apply_cloud_settings({"settings": [{"force": {"badge_position": "top"}, "reason": "旧限制"}]})
    assert s.forced == {} and s.badge_position == "bottom"
    assert w.set_badge.currentData() == "bottom" and w.toasts == []
    assert json.loads((home / "config.json").read_text(encoding="utf-8"))["badge_position"] == "bottom"


def test_rank_rechecked_when_rank_db_newer(mw):
    w, s, eng, cloud, _ = mw
    s.riot_id = "提莫#1"
    s.my_rank = {"as_of": "2026-10-16", "results": [{"band": "mid", "band_name": "中分", "top": 0.2,
                                                    "region_name": "艾欧尼亚", "games": 50}]}
    eng.set_band("mid")
    calls = []

    def fake(silent=False):
        calls.append(silent)
        w._rank_done("提莫#1", {"as_of": "2026-10-22", "results": [{"band": "high", "band_name": "前 5%", "top": 0.031,
                                                                   "region_name": "艾欧尼亚", "games": 80}]}, silent)
    w._query_rank = fake
    cfg = cloud._merge({"rank": {"as_of": "2026-10-22"}})
    assert w.recheck_rank_if_newer(cfg) and s.data_band == "high"  # 分段原来跟着查分选的：跟着换
    assert w.toasts and "前 5%" in w.toasts[0] and "中分" in w.toasts[0]
    assert not w.recheck_rank_if_newer(cfg) and calls == [True]   # 同一个日期只重查一次
    assert not w.recheck_rank_if_newer(cloud._merge({"rank": {"as_of": "2026-10-20"}}))
    # 玩家自己选了别的分段：只提示，不换
    s.my_rank = {"as_of": "2026-10-01", "results": [{"band": "mid", "band_name": "中分", "top": 0.2,
                                                    "region_name": "艾欧尼亚", "games": 50}]}
    s.extra.pop("rank_rechecked")
    eng.set_band("casual")
    w.recheck_rank_if_newer(cfg)
    assert s.data_band == "casual" and len(w.toasts) == 2
    # 没填过查分账号：不查
    s.riot_id = ""
    s.extra.pop("rank_rechecked")
    assert not w.recheck_rank_if_newer(cfg)
