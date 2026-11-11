"""云控换官网地址（site.urls，1.1.3）：新地址在前，内置 aramhex.com 永远兜底。"""
import json

import pytest

from lolhex import BACKUP_SITES, WEBSITE, cloud, logupload, patch, update

NEW = "https://new.example.cn"
CN = "https://aramhex.cn"


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise cloud.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.data


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(cloud, "_cache", None)
    return tmp_path


def use(monkeypatch, raw):
    monkeypatch.setattr(cloud, "_cache", cloud._merge(raw))


def test_default_is_builtin_site():
    assert BACKUP_SITES == (CN,)
    assert cloud.sites() == [WEBSITE, CN] and cloud.site() == WEBSITE
    cfg = cloud.get()
    assert cfg["rank"]["api"] == f"{WEBSITE}/api/rank" and "logs" not in cfg


def test_site_urls_are_validated():
    bad = ["http://x.cn", "https://x.cn/path", "https://localhost", "ftp://x.cn", 3, None, WEBSITE]
    assert cloud._merge({"site": {"urls": bad}})["site"]["urls"] == []
    assert cloud._merge({"site": {"urls": "https://x.cn"}})["site"]["urls"] == []      # 不是列表
    many = ["https://A.cn/", "https://a.cn", "https://b.cn", "https://c.cn:8443", "https://d.cn"]
    assert cloud._merge({"site": {"urls": many}})["site"]["urls"] == ["https://a.cn", "https://b.cn", "https://c.cn:8443"]


def test_rank_data_site_changes_cannot_restore_log_upload(monkeypatch):
    use(monkeypatch, {"site": {"urls": [NEW]}})
    assert cloud.sites() == [NEW, WEBSITE, CN] and cloud.site() == WEBSITE
    assert cloud.get()["rank"]["api"] == f"{NEW}/api/rank" and "logs" not in cloud.get()
    # 查分仍可迁移；日志地址不管新站、老站或第三方都不再接受。
    cfg = cloud._merge({"site": {"urls": [NEW]}, "rank": {"api": "https://rank.example/api"},
                        "logs": {"url": f"{WEBSITE}/api/logs"}})
    assert cfg["rank"]["api"] == "https://rank.example/api" and "logs" not in cfg
    assert "logs" not in cloud._merge({"site": {"urls": [NEW]}, "logs": {"url": "https://evil.example/api/logs"}})
    assert cloud.own(f"{NEW}/u/data") and cloud.own(f"{WEBSITE}/config.json") and not cloud.own("https://github.com/x")


def test_refresh_tries_new_site_first_then_builtin(monkeypatch, home):
    (home / "cloud.json").write_text(json.dumps({"site": {"urls": [NEW]}}), encoding="utf-8")
    tried = []

    def get(url, headers=None, **k):
        tried.append((url, "X-AramHex-Id" in (headers or {})))
        if url.startswith(NEW):
            raise cloud.requests.ConnectionError("down")
        return Resp({"site": {"urls": [NEW]}, "rank": {"as_of": "2026-11-07"}})
    monkeypatch.setattr(cloud.requests, "get", get)
    cfg = cloud.refresh()
    assert [u for u, _ in tried] == [f"{NEW}/config.json", f"{WEBSITE}/config.json"]
    assert not any(sent for _, sent in tried)          # 新老官网都不能带匿名编号。
    assert cfg["rank"]["as_of"] == "2026-11-07" and cloud.site() == WEBSITE
    tried.clear()
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: tried.append(url) or Resp({}))
    cloud.refresh()
    assert tried == [f"{NEW}/config.json"]             # 新站通了就不再问老站
    assert cloud.sites() == [WEBSITE, CN]              # 新站的配置里没写 site：下次回到内置


def test_all_sites_down_keeps_last_config(monkeypatch):
    use(monkeypatch, {"site": {"urls": [NEW]}, "rank": {"as_of": "2026-11-07"}})
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp(None, 503))
    assert cloud.refresh()["rank"]["as_of"] == "2026-11-07"


@pytest.mark.site
def test_legacy_update_helpers_follow_data_sites_but_website_link_stays_fixed(monkeypatch):
    use(monkeypatch, {"site": {"urls": [NEW]}})
    src = patch.sources_for("1.1.3")
    # 仅验证保留的上游辅助函数；定制 UI 不执行原作者程序升级。
    assert src[:2] == [f"{NEW}/u/1.1.3", f"{WEBSITE}/u/1.1.3"]
    assert update.download_page() == f"{WEBSITE}/#download"
    asked = []

    def get(url, **k):
        asked.append(url)
        if url.startswith(NEW):
            raise update.requests.ConnectionError("down")
        return Resp({"version": "1.0.0"})
    monkeypatch.setattr(update.requests, "get", get)
    assert update.check("1.0.0") is None
    assert asked[:2] == [f"{NEW}/u/latest.json", f"{WEBSITE}/u/latest.json"]


def test_log_upload_stays_disabled_after_data_site_migration(monkeypatch):
    use(monkeypatch, {"site": {"urls": [NEW]}})
    assert "logs" not in cloud.get()
    assert logupload.maybe_upload(object(), {"logs": {"url": f"{NEW}/api/logs", "request": ["old-id"]}}) == []


def test_builtin_backup_site_when_com_is_down(monkeypatch):
    # 云控还没送到（没有 site），aramhex.com 又连不上：去试内置的 aramhex.cn
    tried = []

    def get(url, **k):
        tried.append(url)
        if url.startswith(WEBSITE):
            raise cloud.requests.ConnectionError("blocked")
        return Resp({"rank": {"as_of": "2026-11-07"}})
    monkeypatch.setattr(cloud.requests, "get", get)
    assert cloud.refresh()["rank"]["as_of"] == "2026-11-07"
    assert tried == [f"{WEBSITE}/config.json", f"{CN}/config.json"]
    # 云控写了 aramhex.cn：它排第一，不重复
    use(monkeypatch, {"site": {"urls": [CN]}})
    assert cloud.sites() == [CN, WEBSITE] and cloud.get()["rank"]["api"] == f"{CN}/api/rank"
