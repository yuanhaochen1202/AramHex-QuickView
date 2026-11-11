"""云控配置和查分客户端。"""
import pytest

from lolhex import cloud, rank


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise cloud.requests.HTTPError(str(self.status_code))

    def json(self):
        if self.data is None:
            raise ValueError("not json")
        return self.data


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(cloud, "_cache", None)


def test_cloud_defaults_and_bad_values(monkeypatch):
    assert cloud.get()["rank"]["enabled"] is True
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"rank": {"enabled": False, "message": "维护中", "evil": {"x": 1}},
                                                                      "notice": "不是字典", "min_version": "1.2.0"}))
    cfg = cloud.refresh()
    assert cfg["rank"]["enabled"] is False and "evil" not in cfg["rank"] and cfg["notice"] == {} and cfg["min_version"] == ""


def test_rank_lookup_paths(monkeypatch):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(headers)
        rid = params["id"]
        if rid == "a#1":
            return Resp({"as_of": "2026-10-16", "results": [{"region": "HN1", "region_name": "艾欧尼亚", "top": 0.031,
                                                              "band": "high", "band_name": "高分", "games": 120}]})
        if rid == "slow#1":
            return Resp({"error": "查得太快了，请一分钟后再试"}, 429)
        if rid == "down#1":
            return Resp(None, 502)
        return Resp({"error": "没有查到"}, 404)
    monkeypatch.setattr(rank.requests, "get", get)
    res = rank.lookup("a#1")
    assert calls[0]["X-AramHex"] and rank.describe(res).startswith("前 3.1%（高分）· 艾欧尼亚") and "截至 10-16" in rank.describe(res)
    for rid, msg in (("slow#1", "一分钟"), ("down#1", "暂时不可用"), ("x#1", "没有查到"), ("没编号", "名字#编号")):
        with pytest.raises(rank.RankError, match=msg):
            rank.lookup(rid)
    monkeypatch.setattr(cloud, "_cache", cloud._merge({"rank": {"enabled": False, "message": "查询维护中"}}))
    with pytest.raises(rank.RankError, match="维护中"):
        rank.lookup("a#1")


def test_rank_id_from_client_copy_is_cleaned(monkeypatch):
    sent = []
    monkeypatch.setattr(rank.requests, "get", lambda url, params=None, headers=None, timeout=None:
                        sent.append(params["id"]) or Resp({"error": "没有查到"}, 404))
    for raw in ("⁦魅 惑⁩#⁦60051⁩", " 魅 惑 ＃ 60051 ", "﻿魅 惑#60051​"):
        with pytest.raises(rank.RankError):
            rank.lookup(raw)
    assert sent == ["魅 惑#60051"] * 3  # 名字中间的空格保留
    with pytest.raises(rank.RankError, match="名字#编号"):
        rank.lookup("⁦提莫⁩#⁦⁩")


def test_rank_unreachable(monkeypatch):
    def boom(*a, **k):
        raise rank.requests.ConnectionError("x")
    monkeypatch.setattr(rank.requests, "get", boom)
    with pytest.raises(rank.RankError, match="连不上"):
        rank.lookup("a#1")


def test_cloud_data_mirrors_allowed_but_program_mirrors_and_features_ignored(monkeypatch):
    from lolhex import patch
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"mirrors": {
        "update": ["https://new.example/u/{version}/", "http://insecure.example/x"], "data": ["https://new.example/u/data"]},
        "features": {"autoshot": False}}))
    cloud.refresh()
    src = patch.sources_for("1.2.3")
    assert not any("new.example" in s or "insecure" in s for s in src)
    assert cloud.mirrors("update") == []
    assert cloud.mirrors("data") == ["https://new.example/u/data"]
    assert cloud.feature("autoshot") is True and cloud.feature("other") is True


def test_remote_community_list_cannot_restore_program_controls(monkeypatch):
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"community": [
        {"name": "QQ 群", "text": "123456", "url": "https://qm.qq.com/q/x", "qr": "https://aramhex.com/img/qq.png", "primary": True},
        {"name": "B 站", "url": "javascript:alert(1)"},
        {"text": "没有名字"}, "不是字典",
        {"name": "赞助", "url": "https://afdian.com/a/x", "qr": "http://insecure/qr.png"},
    ]}))
    cloud.refresh()
    c = cloud.community()
    assert c == [] and cloud.primary_link() is None


def test_refresh_has_no_anonymous_identity_state_or_header(monkeypatch, tmp_path):
    from lolhex.config import Settings
    s = Settings.load(tmp_path / "config.json")
    assert not hasattr(s, "install_id") and not hasattr(s, "send_usage")
    assert not hasattr(cloud, "_install_id") and not hasattr(cloud, "set_install_id")
    seen = []
    monkeypatch.setattr(cloud.requests, "get", lambda url, headers=None, **k: seen.append(dict(headers or {})) or Resp({}))
    cloud.refresh()
    cloud.refresh()
    assert len(seen) == 2
    assert all(not ({name.casefold() for name in h} & {"x-aramhex-id", "x-aramhex-ticket"}) for h in seen)
    assert seen[1]["User-Agent"].startswith("aramhex/")


def test_update_policy_from_cloud(monkeypatch):
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"update": {
        "check_minutes": 5, "data_minutes": "720", "rollout_version": "v1.2.0", "rollout_percent": 30}}))
    cloud.refresh()
    assert cloud.check_minutes() == 180 and cloud.data_minutes() == 720
    assert cloud.offer_update("1.2.0", None) and cloud.offer_update("1.3.0", None)
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"update": {"paused": True}}))
    cloud.refresh()
    assert cloud.offer_update("1.3.0", None) and cloud.check_minutes() == 180


def test_bad_cloud_values_never_crash(monkeypatch, tmp_path):
    # 云控里写错一个数（无穷大、超大、NaN、文字）、整个文件不是对象、存下的缓存坏了：都按默认值，不能让程序起不来
    bad = [{"update": {"check_minutes": 1e400, "data_minutes": "inf", "rollout_version": "1.2.0",
                       "rollout_percent": "nan", "paused": "false"}},
           {"update": {"check_minutes": 10 ** 400, "data_minutes": None, "rollout_percent": True}},
           [1, 2], "x", {"notice": {"id": 5, "text": 123}, "rank": {"enabled": "false"}, "features": {"a": "no"}}]
    for raw in bad:
        monkeypatch.setattr(cloud, "_cache", cloud._merge(raw))
        assert cloud.check_minutes() == 180 and cloud.data_minutes() == 360
        assert cloud.offer_update("1.2.0", "abc") is True                    # 文字 "false" 不算暂停，比例认不出按 100
    assert cloud.get()["notice"] == {} and cloud.get()["rank"]["enabled"] is True
    assert cloud.feature("a") is True
    monkeypatch.setattr(cloud, "_cache", None)
    cloud._path().parent.mkdir(parents=True, exist_ok=True)
    cloud._path().write_text("[1, 2, 3]", encoding="utf-8")                  # 存下的云控坏了
    assert cloud.check_minutes() == 180


def test_headers_keep_version_and_strip_identity_for_every_destination():
    # 版本号和 Range 等普通请求头保留；本站、第三方、调用方补传都不能带回编号。
    from lolhex import WEBSITE, __version__
    for other in (f"{WEBSITE}/u/data/latest.json", "https://raw.githubusercontent.com/x/latest.json", "https://cdn.jsdelivr.net/gh/x", "",
                  "https://aramhex.com.evil.example/u/"):
        h = cloud.headers(other, **{"X-AramHex-Id": "old-id", "x-aramhex-id": "old-id2",
                                    "X-ARAMHEX-TICKET": "old-ticket", "x-AramHex-TiCkEt": "old-ticket2",
                                    "Range": "bytes=0-9", "Accept": "application/json"})
        assert h["User-Agent"] == f"aramhex/{__version__}"
        assert not ({name.casefold() for name in h} & {"x-aramhex-id", "x-aramhex-ticket"})
        assert h["Range"] == "bytes=0-9" and h["Accept"] == "application/json"


def test_data_sync_sends_version_header(monkeypatch, tmp_path):
    from lolhex import WEBSITE, __version__
    from lolhex.data import public
    seen = []
    monkeypatch.setattr(public.requests, "get", lambda url, headers=None, **k: seen.append((url, dict(headers or {})))
                        or (_ for _ in ()).throw(public.requests.ConnectionError("x")))
    for base in (f"{WEBSITE}/u/data", "https://cdn.jsdelivr.net/gh/czgreat/aramhex-data@main"):
        try:
            public.PublicStore._sync_from(None, base)
        except public.requests.ConnectionError:
            pass
    assert len(seen) == 2 and all(h["User-Agent"] == f"aramhex/{__version__}" for _, h in seen)
    assert all(not ({name.casefold() for name in h} & {"x-aramhex-id", "x-aramhex-ticket"}) for _, h in seen)
