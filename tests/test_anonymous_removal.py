"""旧版匿名编号及日志回传配置的迁移；真实用户设置必须保留。"""
from dataclasses import fields
import json

import pytest

from lolhex.config import Settings

REMOVED_ANONYMOUS = {"send_usage", "install_id", "logs_sent", "logs_tries", "verbose_seen"}


def assert_removed(settings, saved):
    assert not ({field.name for field in fields(Settings)} & REMOVED_ANONYMOUS)
    assert all(not hasattr(settings, name) for name in REMOVED_ANONYMOUS)
    assert not (set(settings.extra) & REMOVED_ANONYMOUS)
    assert not (set(settings.user_set) & REMOVED_ANONYMOUS)
    assert not (set(saved) & REMOVED_ANONYMOUS)
    assert not (set(saved.get("user_set", [])) & REMOVED_ANONYMOUS)


def test_fresh_install_never_generates_or_persists_anonymous_identity(tmp_path, monkeypatch):
    import secrets

    def forbidden(*args, **kwargs):
        pytest.fail("loading settings must no longer create an anonymous identifier")

    monkeypatch.setattr(secrets, "token_hex", forbidden)
    path = tmp_path / "config.json"
    settings = Settings.load(path)
    assert_removed(settings, json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("usage,identifier", [(True, "0123456789abcdef"), (False, ""), ("yes", 123), (None, [1, 2])])
def test_load_cleans_legacy_anonymous_fields_without_losing_preferences(tmp_path, usage, identifier):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "send_usage": usage, "install_id": identifier, "logs_sent": ["old:ticket"],
        "logs_tries": {"old:ticket": 2}, "verbose_seen": {"old:ticket": 1000},
        "user_set": ["send_usage", "install_id", "logs_sent", "logs_tries", "verbose_seen", "speed"],
        "speed": "saver", "game_lang": "en_US", "check_data_updates": True,
        "riot_id": "TestPlayer#1234", "my_rank": {"as_of": "2026-11-01"},
        "custom_update_last_success": "local-test-time", "future_local_option": {"keep": True},
    }), encoding="utf-8")
    settings = Settings.load(path)
    # 只 load 即完成迁移，不需要使用者点一次保存才清除旧值。
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert_removed(settings, saved)
    assert settings.speed == saved["speed"] == "saver"
    assert saved["game_lang"] == "en_US" and saved["check_data_updates"] is True
    assert saved["riot_id"] == "TestPlayer#1234" and saved["my_rank"] == {"as_of": "2026-11-01"}
    assert saved["custom_update_last_success"] == "local-test-time"
    assert saved["future_local_option"] == {"keep": True} and settings.user_set == ["speed"]
    assert_removed(Settings.load(path), json.loads(path.read_text(encoding="utf-8")))


def test_save_cannot_resurrect_removed_keys_from_old_extra_or_forced_values(tmp_path):
    path = tmp_path / "config.json"
    settings = Settings(speed="low")
    settings.extra.update({name: "old-value" for name in REMOVED_ANONYMOUS})
    settings.extra["future_local_option"] = 123
    settings.forced.update({name: "old-forced-value" for name in REMOVED_ANONYMOUS})
    settings.user_set = [*sorted(REMOVED_ANONYMOUS), "speed"]
    assert settings.save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert not (set(saved) & REMOVED_ANONYMOUS)
    assert saved["user_set"] == ["speed"]
    assert saved["future_local_option"] == 123 and saved["speed"] == "low"
    assert_removed(Settings.load(path), saved)
