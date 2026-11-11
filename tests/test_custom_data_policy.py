"""上游资料仍更新，网络及旧缓存都不能覆盖本地识别设置或重新开放程序升级。"""
import json

from lolhex import cloud, cloudsettings
from lolhex.config import Settings


def upstream_config():
    return {
        "settings": [{"id": "replace-local", "force": {"speed": "low", "game_lang": "en_US",
                                                             "overlay": False, "check_data_updates": False}}],
        "features": {"title_priority": False, "cards_drop_early": False},
        "ui": {"client_badge_top": 999}, "min_version": "99.0",
        "downloads": {"setup": "https://unwanted.example/app.exe"},
        "notice": {"id": "replace-local", "text": "立即安装原版"},
        "community": [{"name": "replacement", "url": "https://unwanted.example"}],
        "logs": {"request": ["install:test"], "verbose": ["install:test"], "url": "https://aramhex.com/api/logs"},
        "site": {"urls": ["https://newdata.example"]},
        "mirrors": {"data": ["https://data.example/stats"], "official": ["https://data.example/official"],
                    "update": ["https://unwanted.example/{version}"]},
        "update": {"data_minutes": 120, "official_minutes": 1440, "check_minutes": 30,
                   "rollout_percent": 0, "paused": True},
        "event": {"mode": "first-prismatic", "label": "首次棱彩"},
        "augments": {"aliases": {"1001": ["修正译名"]}, "rarity_fix": {"1001": "gold"}},
        "rank": {"enabled": True, "as_of": "2026-11-07", "api": "https://aramhex.com/api/rank"},
    }


def assert_data_only(cfg):
    assert cfg["settings"] == [] and cfg["features"] == {}
    assert cfg["ui"] == cloud.DEFAULT["ui"]
    assert cfg["downloads"] == {} and cfg["min_version"] == ""
    assert cfg["notice"] == {} and cfg["community"] == []
    assert "logs" not in cfg and cfg["mirrors"]["update"] == []
    assert cfg["mirrors"]["data"] == ["https://data.example/stats"]
    assert cfg["mirrors"]["official"] == ["https://data.example/official"]
    assert cfg["update"]["data_minutes"] == 120 and cfg["update"]["official_minutes"] == 1440
    assert cfg["update"]["check_minutes"] == 180 and cfg["update"]["paused"] is False
    assert cfg["event"]["mode"] == "first-prismatic"
    assert cfg["augments"]["aliases"]["1001"] == ["修正译名"]
    assert cfg["rank"]["as_of"] == "2026-11-07"
    settings = Settings(game_lang="zh_CN", speed="standard", check_data_updates=True)
    assert cloudsettings.apply(settings, cfg, "1.1.5")["changed"] == []
    assert settings.game_lang == "zh_CN" and settings.speed == "standard"
    assert settings.check_data_updates and settings.overlay


def test_old_cached_controls_are_ignored_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    (tmp_path / "cloud.json").write_text(json.dumps(upstream_config()), encoding="utf-8")
    monkeypatch.setattr(cloud, "_cache", None)
    assert_data_only(cloud.get())
    assert cloud.feature("title_priority") is True
    assert cloud.check_minutes() == 180


def test_network_refresh_saves_only_allowed_data_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return upstream_config()
    monkeypatch.setattr(cloud.requests, "get", lambda *a, **k: Response())
    assert_data_only(cloud.refresh())
    cached = json.loads((tmp_path / "cloud.json").read_text(encoding="utf-8"))
    assert set(cached) <= {"site", "event", "augments", "mirrors", "update", "rank"}
    assert "update" not in cached["mirrors"] and set(cached["update"]) == {"data_minutes", "official_minutes"}
    monkeypatch.setattr(cloud, "_cache", None)
    assert_data_only(cloud.get())


def test_filtered_config_releases_previous_in_memory_forced_settings():
    settings = Settings(game_lang="en_US")
    settings.forced = {"game_lang": "zh_CN"}
    result = cloudsettings.apply(settings, cloud._merge(upstream_config()), "1.1.5")
    assert result["changed"] == ["game_lang"]
    assert settings.game_lang == "zh_CN" and settings.forced == {}


def test_data_routes_migrate_while_website_and_update_routes_stay_fixed(monkeypatch, tmp_path):
    from lolhex.data.public import PublicStore
    monkeypatch.setattr(cloud, "_cache", cloud._merge(upstream_config()))
    assert cloud.site() == "https://aramhex.com"
    assert cloud.sites() == ["https://newdata.example", "https://aramhex.com", "https://aramhex.cn"]
    store = PublicStore(tmp_path / "data", check_updates=True, managed_official=True)
    assert store.check_updates
    assert store.base_urls[:2] == ["https://data.example/stats", "https://newdata.example/u/data"]
    assert cloud.mirrors("update") == []
    assert cloud.event() == {"mode": "first-prismatic", "label": "首次棱彩"}
    assert cloud.data_minutes() == 120 and cloud.official_minutes() == 1440
