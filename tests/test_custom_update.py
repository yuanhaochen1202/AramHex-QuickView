"""Custom updates use signed releases and bounded, resumable direct downloads."""

import base64
import gzip
import hashlib
import json

import pytest
import requests

import lolhex
from lolhex import custom_update as updater
from lolhex import update_signature


# Public RFC 8032 test material, never a production release credential.
TEST_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
TEST_PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
SOURCE = "https://updates.example.invalid"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_info(data):
    packed = gzip.compress(data, mtime=0)
    return {"size": len(data), "sha256": sha(data),
            "download_size": len(packed), "download_sha256": sha(packed)}, packed


def manifest(version="2.0.0", contents=None):
    contents = contents or {"AramHex.exe": b"MZ new executable"}
    return {"schema": 1, "product": "aramhex-custom", "channel": "user",
            "version": version, "notes": "经过验证的用户版",
            "files": {name: file_info(data)[0] for name, data in contents.items()}}


def envelope(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return json.dumps({"payload": base64.b64encode(payload).decode("ascii"),
                       "signature": base64.b64encode(update_signature.sign(TEST_SEED, payload)).decode("ascii")}).encode("utf-8")


class Response:
    def __init__(self, body=b"", status=200, headers=None, chunks=None):
        self.body, self.status_code = body, status
        self.headers, self.chunks = headers or {}, chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, size):
        if self.chunks is not None:
            for chunk in self.chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
        else:
            for start in range(0, len(self.body), size):
                yield self.body[start:start + size]


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert self.responses, "unexpected network request"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def isolated_release(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ARAMHEX_HOME", str(home))
    monkeypatch.setattr(updater, "app_home", lambda: home)
    monkeypatch.setattr(updater.distribution, "CHANNEL", "user")
    monkeypatch.setattr(updater.release_config, "SIGNING_PUBLIC_KEY_HEX", TEST_PUBLIC.hex())
    monkeypatch.setattr(updater.release_config, "UPDATE_BASE_URLS", (SOURCE,))
    monkeypatch.setattr(lolhex, "__version__", "1.0.0")


def test_valid_release_signature_and_exact_payload():
    value = manifest()
    assert updater.verify_envelope(envelope(value)) == value


def test_unsigned_payload_is_never_parsed(monkeypatch):
    # A JSON parsing error would reveal that the untrusted payload was parsed
    # before its signature was checked.
    monkeypatch.setattr(updater.update_signature, "verify", lambda *args: False)
    raw = json.dumps({"payload": base64.b64encode(b"not json").decode(),
                      "signature": base64.b64encode(b"x" * 64).decode()}).encode()
    with pytest.raises(updater.UpdateError, match="签名验证失败"):
        updater.verify_envelope(raw)


def test_payload_tampering_is_rejected():
    data = json.loads(envelope(manifest()))
    payload = json.loads(base64.b64decode(data["payload"]))
    payload["version"] = "999.0.0"
    data["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(updater.UpdateError, match="签名验证失败"):
        updater.verify_envelope(json.dumps(data).encode())


@pytest.mark.parametrize("raw", [b"", b"{}", b"[]", b"not-json", b'\xff',
                                  b'{"payload":"a","payload":"b","signature":""}',
                                  b'{"payload":"!","signature":"!"}',
                                  b'{"payload":{},"signature":[]}'])
def test_invalid_envelope_rejected(raw):
    with pytest.raises(updater.UpdateError):
        updater.verify_envelope(raw)


def test_signed_duplicate_payload_field_rejected():
    payload = b'{"schema":1,"schema":2}'
    raw = json.dumps({"payload": base64.b64encode(payload).decode(),
                      "signature": base64.b64encode(update_signature.sign(TEST_SEED, payload)).decode()}).encode()
    with pytest.raises(updater.UpdateError):
        updater.verify_envelope(raw)


def test_envelope_size_is_limited(monkeypatch):
    monkeypatch.setattr(updater, "MAX_MANIFEST", 3)
    with pytest.raises(updater.UpdateError, match="大小"):
        updater.verify_envelope(b"1234")


@pytest.mark.parametrize("field,value", [("schema", True), ("schema", "1"), ("schema", 2),
                                         ("product", "aramhex"), ("channel", "maintainer"),
                                         ("channel", "upstream"), ("notes", []),
                                         ("notes", "x" * 8001)])
def test_signed_manifest_for_wrong_product_channel_or_schema_rejected(field, value):
    release = manifest()
    release[field] = value
    with pytest.raises(updater.UpdateError):
        updater.verify_envelope(envelope(release))


@pytest.mark.parametrize("version", [None, 1, "1.1", "v1.1.5", "1.1.5-beta", "1.1.5.3.4",
                                    "1.000001.0", "../1.1.5", "1.1.5\n"])
def test_version_format_is_strict(version):
    release = manifest()
    release["version"] = version
    with pytest.raises(updater.UpdateError):
        updater.validate_manifest(release)


@pytest.mark.parametrize("name", ["../AramHex.exe", "/AramHex.exe", "C:/AramHex.exe",
    "_internal/../secret", "_internal//foo", "_internal/foo.", "_internal/foo ",
    "_internal/foo:stream", "_internal/%2e%2e/foo", "_internal/foo?x", "_internal/foo\x00",
    "_internal/CON", "_internal/aux.txt", "_internal/NUL.dll", "_internal/LPT9.bin",
    "_internal/CONIN$", "_internal/CONOUT$", "_internal/COM¹.dll", "_internal/LPT²",
    "_internal/UserData/config.json", "_internal/screenshots/a.png", "_internal/.git/index",
    "config.json", "portable.txt", "AramHex.exe/child", "_INTERNAL/a.dll"])
def test_windows_special_paths_and_user_data_are_rejected(name):
    with pytest.raises(updater.UpdateError):
        updater.relative_path(name)


def test_manifest_rejects_case_aliases():
    release = manifest(contents={"AramHex.exe": b"exe", "_internal/A.dll": b"a", "_internal/a.dll": b"b"})
    with pytest.raises(updater.UpdateError, match="大小写"):
        updater.validate_manifest(release)


def test_manifest_rejects_file_directory_aliases():
    release = manifest(contents={"AramHex.exe": b"exe", "_internal/A": b"a", "_internal/a/b.dll": b"b"})
    with pytest.raises(updater.UpdateError):
        updater.validate_manifest(release)


@pytest.mark.parametrize("field,value", [("size", True), ("size", -1), ("size", updater.MAX_FILE + 1),
                                         ("download_size", False), ("download_size", 19),
                                         ("download_size", updater.MAX_FILE + 1),
                                         ("sha256", "A" * 64), ("sha256", "x" * 64),
                                         ("download_sha256", ""), ("download_sha256", 5)])
def test_file_hash_and_size_bounds(field, value):
    release = manifest()
    release["files"]["AramHex.exe"][field] = value
    with pytest.raises(updater.UpdateError):
        updater.validate_manifest(release)


def test_file_count_and_main_executable_are_required(monkeypatch):
    with pytest.raises(updater.UpdateError):
        updater.validate_manifest(manifest(contents={"LICENSE": b"license"}))
    monkeypatch.setattr(updater, "MAX_FILES", 1)
    with pytest.raises(updater.UpdateError):
        updater.validate_manifest(manifest(contents={"AramHex.exe": b"exe", "LICENSE": b"license"}))


def test_uncompressed_total_is_bounded(monkeypatch):
    monkeypatch.setattr(updater, "MAX_TOTAL", 150)
    release = manifest(contents={"AramHex.exe": b"x" * 100, "LICENSE": b"y" * 100})
    assert sum(item["download_size"] for item in release["files"].values()) < 150
    with pytest.raises(updater.UpdateError):
        updater.validate_manifest(release)


def test_download_total_is_bounded_too(monkeypatch):
    monkeypatch.setattr(updater, "MAX_TOTAL", 30)
    release = manifest(contents={"AramHex.exe": b"x", "LICENSE": b"y"})
    assert sum(item["size"] for item in release["files"].values()) < 30
    with pytest.raises(updater.UpdateError):
        updater.validate_manifest(release)


@pytest.mark.parametrize("url", ["http://updates.example.invalid", "https://user:secret@updates.example.invalid",
                                 "https://updates.example.invalid:444", "https://updates.example.invalid/?x=1",
                                 "https://updates.example.invalid/#x", "https://"])
def test_only_fixed_https_sources_are_accepted(monkeypatch, url):
    monkeypatch.setattr(updater.release_config, "UPDATE_BASE_URLS", (url,))
    with pytest.raises(updater.UpdateError):
        updater._sources()


def test_real_session_ignores_environment_proxy_and_checks_tls():
    with updater._session() as session:
        assert session.trust_env is False
        assert session.verify is True
        assert session.headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("response", [Response(status=301, headers={"Location": "https://elsewhere.invalid"}),
                                    Response(status=403), Response(status=503),
                                    requests.ConnectionError("offline")])
def test_check_failure_never_reports_current(monkeypatch, response):
    session = Session(response)
    monkeypatch.setattr(updater, "_session", lambda: session)
    result = updater.check("1.0.0")
    assert result["status"] == "unavailable"
    assert session.calls[0][0] == SOURCE + "/channels/user/latest.json"
    assert session.calls[0][1]["allow_redirects"] is False


def test_oversized_manifest_response_never_reaches_signature_check(monkeypatch):
    monkeypatch.setattr(updater, "MAX_MANIFEST", 3)
    monkeypatch.setattr(updater, "_session", lambda: Session(Response(chunks=[b"123", b"4"])))
    def should_not_verify(*args):
        pytest.fail("oversized response reached signature parser")
    monkeypatch.setattr(updater, "verify_envelope", should_not_verify)
    assert updater.check("1.0.0")["status"] == "unavailable"


def test_not_yet_published_is_distinct_from_current(monkeypatch):
    monkeypatch.setattr(updater, "_session", lambda: Session(Response(status=404)))
    assert updater.check("1.0.0")["status"] == "unpublished"


def test_latest_is_highest_verified_release_across_sources(monkeypatch):
    sources = (SOURCE, "https://backup.example.invalid")
    monkeypatch.setattr(updater.release_config, "UPDATE_BASE_URLS", sources)
    session = Session(Response(envelope(manifest("2.0.0"))), Response(envelope(manifest("3.0.0"))))
    monkeypatch.setattr(updater, "_session", lambda: session)
    result = updater.check("1.0.0")
    assert result["status"] == "newer" and result["version"] == "3.0.0"
    assert len(session.calls) == 2


def test_highest_seen_release_cannot_move_backwards(monkeypatch):
    updater._remember("3.0.0")
    monkeypatch.setattr(updater, "_session", lambda: Session(Response(envelope(manifest("2.0.0")))))
    assert updater.check("1.0.0")["status"] == "unavailable"
    assert json.loads(updater._history_path().read_text())["highest_version"] == "3.0.0"


def test_signed_updates_advance_through_upstream_baselines_and_custom_revisions(monkeypatch):
    current = "1.1.5.999"
    versions = ("1.1.6", "1.1.6.1", "1.1.6.2", "1.1.6.10", "1.1.7")
    session = Session(*(Response(envelope(manifest(version))) for version in versions),
                      Response(envelope(manifest("1.1.6.999"))))
    monkeypatch.setattr(updater, "_session", lambda: session)
    updater._remember(current)
    for version in versions:
        result = updater.check(current)
        assert result["status"] == "newer" and result["version"] == version
        assert json.loads(updater._history_path().read_text())["highest_version"] == version
        current = version
    # Resetting the custom suffix for a newer upstream must not reset rollback protection.
    assert updater.check(current)["status"] == "unavailable"
    assert json.loads(updater._history_path().read_text())["highest_version"] == "1.1.7"
    assert len(session.calls) == len(versions) + 1 and not session.responses


@pytest.mark.parametrize("current,published", [("1.1.6", "1.1.6.0"), ("1.1.6.0", "1.1.6")])
def test_baseline_and_zero_custom_suffix_do_not_trigger_an_update(monkeypatch, current, published):
    updater._remember(current)
    session = Session(Response(envelope(manifest(published))))
    monkeypatch.setattr(updater, "_session", lambda: session)
    result = updater.check(current)
    assert result["status"] == "current" and result["version"] == published


def test_damaged_local_state_does_not_crash_version_check(monkeypatch):
    updater._history_path().write_text("[]", encoding="utf-8")
    monkeypatch.setattr(updater, "_session", lambda: Session(Response(envelope(manifest()))))
    assert updater.check("1.0.0")["status"] in {"newer", "unavailable"}


def test_maintainer_never_checks_or_downloads_user_release(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.distribution, "CHANNEL", "maintainer")
    def no_network():
        pytest.fail("maintainer contacted custom update channel")
    monkeypatch.setattr(updater, "_session", no_network)
    assert updater.check("1.0.0")["status"] == "disabled"
    with pytest.raises(updater.UpdateError):
        updater.prepare({"envelope": envelope(manifest())}, root=tmp_path)


def test_blob_download_uses_only_hashed_fixed_url(tmp_path):
    spec, packed = file_info(b"MZ new executable")
    session = Session(Response(packed))
    path = updater._download_blob(spec, tmp_path, session, (SOURCE,))
    assert path.read_bytes() == packed
    assert session.calls[0][0] == SOURCE + "/objects/" + spec["download_sha256"] + ".gz"
    assert session.calls[0][1]["allow_redirects"] is False


def test_partial_download_resumes_with_exact_range(tmp_path):
    spec, packed = file_info(b"MZ new executable")
    partial = tmp_path / (spec["download_sha256"] + ".part")
    partial.write_bytes(packed[:7])
    session = Session(Response(packed[7:], status=206,
                              headers={"Content-Range": f"bytes 7-{len(packed)-1}/{len(packed)}"}))
    result = updater._download_blob(spec, tmp_path, session, (SOURCE,))
    assert result.read_bytes() == packed and not partial.exists()
    assert session.calls[0][1]["headers"] == {"Range": "bytes=7-"}


@pytest.mark.parametrize("content_range", ["", "bytes 0-10/11", "bytes 7-10/*", "bytes 8-20/21"])
def test_inexact_content_range_is_rejected_without_appending(tmp_path, content_range):
    spec, packed = file_info(b"MZ new executable")
    partial = tmp_path / (spec["download_sha256"] + ".part")
    partial.write_bytes(packed[:7])
    session = Session(Response(packed[7:], status=206, headers={"Content-Range": content_range}))
    with pytest.raises(updater.UpdateError):
        updater._download_blob(spec, tmp_path, session, (SOURCE,))
    assert partial.read_bytes() == packed[:7]


def test_range_ignored_with_200_restarts_instead_of_appending(tmp_path):
    spec, packed = file_info(b"MZ new executable")
    (tmp_path / (spec["download_sha256"] + ".part")).write_bytes(packed[:7])
    result = updater._download_blob(spec, tmp_path, Session(Response(packed)), (SOURCE,))
    assert result.read_bytes() == packed


def test_range_416_clears_partial_and_can_restart_on_next_source(tmp_path):
    spec, packed = file_info(b"MZ new executable")
    partial = tmp_path / (spec["download_sha256"] + ".part")
    partial.write_bytes(packed[:7])
    session = Session(Response(status=416), Response(packed))
    result = updater._download_blob(spec, tmp_path, session, (SOURCE, "https://backup.example.invalid"))
    assert result.read_bytes() == packed and not partial.exists()
    assert session.calls[1][1]["headers"] == {}


def test_interrupted_transfer_keeps_only_received_bytes_for_resume(tmp_path):
    spec, packed = file_info(b"MZ new executable")
    session = Session(Response(chunks=[packed[:7], requests.ConnectionError("interrupted")]))
    with pytest.raises(updater.UpdateError):
        updater._download_blob(spec, tmp_path, session, (SOURCE,))
    assert (tmp_path / (spec["download_sha256"] + ".part")).read_bytes() == packed[:7]


def test_complete_corrupt_transfer_is_deleted(tmp_path):
    spec, packed = file_info(b"MZ new executable")
    session = Session(Response(bytes([packed[0] ^ 1]) + packed[1:]))
    with pytest.raises(updater.UpdateError):
        updater._download_blob(spec, tmp_path, session, (SOURCE,))
    assert not (tmp_path / (spec["download_sha256"] + ".part")).exists()
    assert not (tmp_path / (spec["download_sha256"] + ".gz")).exists()


def test_redirect_during_download_is_not_followed(tmp_path):
    spec, _ = file_info(b"MZ new executable")
    session = Session(Response(status=302, headers={"Location": "https://upstream.invalid/program.exe"}))
    with pytest.raises(updater.UpdateError):
        updater._download_blob(spec, tmp_path, session, (SOURCE,))
    assert len(session.calls) == 1 and session.calls[0][1]["allow_redirects"] is False


def test_complete_valid_partial_needs_no_download(tmp_path):
    spec, packed = file_info(b"MZ new executable")
    (tmp_path / (spec["download_sha256"] + ".part")).write_bytes(packed)
    session = Session()
    assert updater._download_blob(spec, tmp_path, session, (SOURCE,)).read_bytes() == packed
    assert not session.calls


def test_uncompressed_size_limit_prevents_gzip_expansion(tmp_path):
    content = b"x" * (256 * 1024)
    spec, packed = file_info(content)
    spec["size"] = 10
    blob, destination = tmp_path / "blob.gz", tmp_path / "AramHex.exe"
    blob.write_bytes(packed)
    destination.write_bytes(b"old file")
    with pytest.raises(updater.UpdateError, match="超过"):
        updater._unpack(blob, destination, spec)
    assert destination.read_bytes() == b"old file"
    assert not destination.with_name(destination.name + ".partial").exists()


def test_uncompressed_hash_is_verified_before_replacing_file(tmp_path):
    spec, packed = file_info(b"expected executable")
    spec["sha256"] = "0" * 64
    blob, destination = tmp_path / "blob.gz", tmp_path / "AramHex.exe"
    blob.write_bytes(packed)
    destination.write_bytes(b"old file")
    with pytest.raises(updater.UpdateError, match="校验失败"):
        updater._unpack(blob, destination, spec)
    assert destination.read_bytes() == b"old file"


def test_prepare_stages_only_verified_changes_preserving_installed_files(monkeypatch, tmp_path):
    from lolhex import custom_install
    monkeypatch.setattr(custom_install, "recover", lambda root: {"status": "clean"})
    root = tmp_path / "installation"
    (root / "_internal").mkdir(parents=True)
    (root / "AramHex.exe").write_bytes(b"old executable")
    (root / "_internal" / "library.dll").write_bytes(b"unchanged library")
    (root / "config.json").write_bytes(b"private user settings")
    contents = {"AramHex.exe": b"new executable", "_internal/library.dll": b"unchanged library"}
    release = manifest(contents=contents)
    session = Session(Response(file_info(contents["AramHex.exe"])[1]))
    monkeypatch.setattr(updater, "_session", lambda: session)
    result = updater.prepare({"envelope": envelope(release), "version": "999.9.9"}, root=root)
    assert set(result["files"]) == {"AramHex.exe"}
    assert result["version"] == "2.0.0" and result["channel"] == "user"
    assert (result["staging"] / "AramHex.exe").read_bytes() == b"new executable"
    assert (root / "AramHex.exe").read_bytes() == b"old executable"
    assert (root / "_internal" / "library.dll").read_bytes() == b"unchanged library"
    assert (root / "config.json").read_bytes() == b"private user settings"
    assert len(session.calls) == 1


def test_prepare_blocks_incomplete_previous_rollback_before_network(monkeypatch, tmp_path):
    from lolhex import custom_install
    monkeypatch.setattr(custom_install, "recover", lambda root: {"status": "blocked"})
    def no_network():
        pytest.fail("download attempted before rollback recovery")
    monkeypatch.setattr(updater, "_session", no_network)
    with pytest.raises(updater.UpdateError, match="恢复"):
        updater.prepare({"envelope": envelope(manifest())}, root=tmp_path)


@pytest.mark.parametrize("version", ["0.9.0", "1.0.0", "1.0.0.0"])
def test_prepare_rejects_same_or_older_installed_version(monkeypatch, tmp_path, version):
    def no_network():
        pytest.fail("download attempted for non-newer release")
    monkeypatch.setattr(updater, "_session", no_network)
    with pytest.raises(updater.UpdateError, match="不高于"):
        updater.prepare({"envelope": envelope(manifest(version))}, root=tmp_path)


def test_prepare_rejects_pending_release_below_highest_seen(monkeypatch, tmp_path):
    from lolhex import custom_install
    updater._remember("3.0.0")
    monkeypatch.setattr(custom_install, "recover", lambda root: {"status": "clean"})
    def no_network():
        pytest.fail("stale pending release was downloaded after newer release was seen")
    monkeypatch.setattr(updater, "_session", no_network)
    with pytest.raises(updater.UpdateError):
        updater.prepare({"envelope": envelope(manifest("2.0.0"))}, root=tmp_path)
