"""定制版过滤上游网盘链接；保留对旧版下载组件及其隔离边界的验证。"""
import hashlib

import pytest

from lolhex import update


class Resp:
    def __init__(self, data=None, chunks=(), status=200, length=None):
        self.data, self.chunks, self.status_code = data, chunks, status
        self.headers = {"Content-Length": str(length)} if length else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise update.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.data

    def iter_content(self, n):
        yield from self.chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_pan_links_from_network_and_cache_are_filtered(monkeypatch, tmp_path):
    from lolhex import cloud
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setattr(cloud, "_cache", None)
    monkeypatch.setattr(cloud.requests, "get", lambda url, **k: Resp({"downloads": {"setup": "https://pan/s", "portable": ""}}))
    assert update.pan_links() == {}

    def boom(url, **k):
        raise cloud.requests.ConnectionError("x")
    monkeypatch.setattr(cloud.requests, "get", boom)
    monkeypatch.setattr(cloud, "_cache", None)
    assert update.pan_links() == {}  # 离线缓存同样过滤程序下载链接


def test_download_gives_up_when_slow(monkeypatch, tmp_path):
    clock = [0.0]
    import time

    def chunks():
        for _ in range(10):
            clock[0] += 5
            yield b"x" * 1024
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(update.requests, "get", lambda url, **k: Resp(chunks=chunks(), length=10 ** 8))
    info = {"file": "AramHex-Setup-9.exe", "url": "u", "sha256": "0" * 64, "size": 10 ** 8}
    with pytest.raises(update.SlowDownload):
        update.download(info, folder=tmp_path)
    assert not (tmp_path / info["file"]).exists()


def test_download_ok_when_fast(monkeypatch, tmp_path):
    body = b"y" * 4096
    monkeypatch.setattr(update.requests, "get", lambda url, **k: Resp(chunks=[body], length=len(body)))
    info = {"file": "AramHex-Setup-9.exe", "url": "u", "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    assert update.download(info, folder=tmp_path).read_bytes() == body


def test_disabled_legacy_worker_never_offers_a_netdisk_fallback(monkeypatch):
    pytest.importorskip("PySide6")
    from lolhex import patch
    from lolhex.ui import mainwindow

    def unexpected(*args, **kwargs):
        pytest.fail("The disabled upstream download workflow was invoked")

    monkeypatch.setattr(update, "pan_links", unexpected)
    monkeypatch.setattr(update, "download", unexpected)
    monkeypatch.setattr(patch, "prepare", unexpected)
    errors, links = [], []
    worker = mainwindow.UpdateWorker("download", info={"version": "9.9.9"})
    worker.manual.connect(lambda reason, urls: links.append(urls))
    worker.failed.connect(errors.append)
    worker.run()
    assert errors == [mainwindow.distribution.program_update_note()]
    assert links == []
