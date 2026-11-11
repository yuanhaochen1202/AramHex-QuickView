"""官网镜像 latest.json：有就用（带安装包地址和 SHA-256），不通再走 GitHub。"""
import pytest

from lolhex import update

SHA = "a" * 64


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise update.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.data


@pytest.mark.site
def test_site_gives_full_info(monkeypatch):
    j = {"version": "1.0.1", "notes": "修复", "setup": {"name": "AramHex-Setup-1.0.1.exe", "sha256": SHA, "size": 5,
                                                        "url": "https://github.com/x/AramHex-Setup-1.0.1.exe"}}
    monkeypatch.setattr(update.requests, "get", lambda url, **k: Resp(j))
    info = update.check("1.0.0", site="https://site/u/latest.json")
    assert info["version"] == "1.0.1" and info["sha256"] == SHA and info["url"].endswith(".exe") and not info["mirror"]
    assert update.check("1.0.1", site="https://site/u/latest.json") is None


@pytest.mark.site
def test_site_down_falls_back_to_github(monkeypatch):
    calls = []

    def get(url, **k):
        calls.append(url)
        if "site" in url:
            return Resp({}, 503)
        raise update.requests.ConnectionError("GitHub 也不通")
    monkeypatch.setattr(update.requests, "get", get)
    assert update.check("1.0.0", site="https://site/u/latest.json", mirrors=[], probe_hosts=()) is None
    assert calls[0].startswith("https://site") and any("api.github.com" in c for c in calls)


@pytest.mark.site
def test_site_without_hash_only_reports_version(monkeypatch):
    monkeypatch.setattr(update.requests, "get", lambda url, **k: Resp({"version": "1.2.0", "setup": {}}))
    info = update.check("1.0.0", site="https://site/u/latest.json")
    assert info["version"] == "1.2.0" and info["url"] is None and info["mirror"]
