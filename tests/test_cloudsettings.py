"""保留纯规则引擎单测；定制版的真实云入口必须屏蔽所有远程设置规则。

直接传给 apply 的合成规则只测试旧规则引擎及设置恢复，不代表网络仍有控制权。
真实网络、缓存和 UI 使用 cloud._merge 过滤后的配置。
"""
import json

import pytest

from lolhex import cloud, cloudsettings
from lolhex.config import Settings

V = "1.1.1"


def local_rules(*rules):
    """隔离规则引擎的输入，不经过定制版已封闭的云配置入口。"""
    return {"settings": list(rules)}


def test_rule_engine_defaults_skip_items_the_player_changed(tmp_path):
    s = Settings.load(tmp_path / "config.json")
    s.mark_user("show_winrate")
    res = cloudsettings.apply(s, local_rules({"defaults": {"speed": "saver", "show_winrate": False}}), V)
    assert res["changed"] == ["speed"] and s.speed == "saver" and s.show_winrate is True
    s.save(tmp_path / "config.json")
    assert Settings.load(tmp_path / "config.json").speed == "saver"  # defaults 写进设置文件


def test_rule_engine_force_is_not_saved_and_restores(tmp_path):
    p = tmp_path / "config.json"
    s = Settings.load(p)
    s.mark_user("overlay")
    res = cloudsettings.apply(s, local_rules({"id": "r1", "force": {"overlay": False}, "reason": "浮窗临时关闭"}), V)
    assert s.overlay is False and res["reasons"] == [("r1", "浮窗临时关闭")]
    s.save(p)
    assert json.loads(p.read_text(encoding="utf-8"))["overlay"] is True  # 文件里还是玩家原来的
    # 仅在纯引擎测试里直接重复规则；生产缓存入口会先去掉这些规则。
    s2 = Settings.load(p)
    cloudsettings.apply(s2, local_rules({"id": "r1", "force": {"overlay": False}}), V)
    assert s2.overlay is False
    # 云控撤掉：恢复
    res = cloudsettings.apply(s2, local_rules(), V)
    assert s2.overlay is True and res["changed"] == ["overlay"] and not s2.forced


def test_rule_engine_versions_percent_and_bad_values(tmp_path):
    s = Settings.load(tmp_path / "config.json")
    cloudsettings.apply(s, local_rules({"versions": ["1.1.2"], "defaults": {"speed": "low"}}), V)
    assert s.speed == "standard"
    cloudsettings.apply(s, local_rules({"percent": 0, "defaults": {"speed": "low"}}), V)
    assert s.speed == "standard"
    bad = {"speed": "极快", "scan_interval_ms": 5, "match_threshold": "80", "overlay": "false",
           "install_id": "ffffffffffffffff", "send_usage": False, "riot_id": "x#1", "nope": 1,
           "smoothing_games": 500.7, "overlay_corner": "top-left"}
    res = cloudsettings.apply(s, local_rules({"defaults": bad}), V)
    assert sorted(res["changed"]) == ["overlay_corner", "smoothing_games"]
    assert s.smoothing_games == 500 and s.overlay_corner == "top-left"
    assert not hasattr(s, "install_id") and not hasattr(s, "send_usage") and s.riot_id == ""


@pytest.mark.parametrize("percent,expected", [(0, "standard"), (50, "standard"), (99, "standard"), (100, "saver")])
def test_rule_engine_no_longer_uses_legacy_install_identity(percent, expected):
    s = Settings()
    # 即使旧调用方动态塞回旧属性，分组也必须按没有编号处理。
    s.install_id, s.send_usage = "0123456789abcdef", True
    cloudsettings.apply(s, local_rules({"id": "half", "percent": percent, "defaults": {"speed": "saver"}}), V)
    assert s.speed == expected


def test_old_config_migration_marks_non_default_as_user_set(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"speed": "low", "show_winrate": True, "install_id": "0123456789abcdef"}), encoding="utf-8")
    s = Settings.load(p)
    assert "speed" in s.user_set and "show_winrate" not in s.user_set
    result = cloudsettings.apply(s, cloud._merge({"settings": [
        {"defaults": {"speed": "saver", "show_winrate": False}, "force": {"speed": "standard"}}
    ]}), V)
    assert result == {"changed": [], "reasons": []}
    assert s.speed == "low" and s.show_winrate is True and not s.forced


@pytest.mark.parametrize("rules", [
    {"defaults": {"speed": "saver", "show_winrate": False}},
    [{"id": "force", "percent": 100, "force": {"overlay": True, "speed": "standard"},
      "reason": "不应显示的远程原因"}],
    ["x", 3, {"force": {"game_lang": "zh_CN", "check_data_updates": False}}],
    "坏的", [{"defaults": "坏的", "force": [1]}],
])
def test_cloud_entry_discards_valid_and_malformed_rules(rules):
    cfg = cloud._merge({"settings": rules})
    assert cfg["settings"] == []
    s = Settings(speed="low", overlay=False, game_lang="en_US", check_data_updates=True)
    assert cloudsettings.apply(s, cfg, V) == {"changed": [], "reasons": []}
    assert (s.speed, s.overlay, s.game_lang, s.check_data_updates) == ("low", False, "en_US", True)
    assert s.forced == {}


def test_old_cached_force_is_filtered_and_restores_user_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    (tmp_path / "cloud.json").write_text(json.dumps({"settings": [
        {"force": {"overlay": False, "speed": "saver"}, "reason": "旧的远程限制"}
    ]}), encoding="utf-8")
    monkeypatch.setattr(cloud, "_cache", None)
    s = Settings(overlay=False, speed="saver")
    s.forced = {"overlay": True, "speed": "low"}
    filtered = cloud.get()
    assert filtered["settings"] == []
    result = cloudsettings.apply(s, filtered, V)
    assert set(result["changed"]) == {"overlay", "speed"} and result["reasons"] == []
    assert s.overlay is True and s.speed == "low" and s.forced == {}
    s.save(tmp_path / "config.json")
    restored = Settings.load(tmp_path / "config.json")
    assert restored.overlay is True and restored.speed == "low"
