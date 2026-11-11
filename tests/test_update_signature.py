"""Release signatures: independent RFC vectors, tampering, and loader isolation."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from lolhex import update_signature as signature


# Public test vectors, NOT release credentials. RFC 8032 section 7.1, tests 1-3:
# https://www.rfc-editor.org/rfc/rfc8032#section-7.1
VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path / "home"))


@pytest.mark.parametrize("values", VECTORS)
def test_rfc8032_known_answers(values):
    seed, public, payload, expected = map(bytes.fromhex, values)
    assert signature.public_key(seed) == public
    assert signature.sign(seed, payload) == expected
    assert signature.verify(public, payload, expected)


@pytest.mark.parametrize("values", VECTORS)
def test_tampered_payload_signature_and_public_key_fail(values):
    _, public, payload, signed = map(bytes.fromhex, values)
    assert not signature.verify(public, payload + b"\x00", signed)
    assert not signature.verify(public, payload, bytes([signed[0] ^ 1]) + signed[1:])
    assert not signature.verify(bytes([public[0] ^ 1]) + public[1:], payload, signed)


@pytest.mark.parametrize("length", [0, 1, 31, 33, 64, 128])
def test_private_seed_length_is_exact(length):
    with pytest.raises(ValueError):
        signature.public_key(b"\x01" * length)
    with pytest.raises(ValueError):
        signature.sign(b"\x01" * length, b"manifest")


@pytest.mark.parametrize("length", [0, 1, 31, 33, 64, 128])
def test_public_key_length_is_exact(length):
    _, _, message, signed = map(bytes.fromhex, VECTORS[0])
    assert not signature.verify(b"\x01" * length, message, signed)


@pytest.mark.parametrize("length", [0, 1, 32, 63, 65, 128])
def test_signature_length_is_exact(length):
    _, public, message, _ = map(bytes.fromhex, VECTORS[0])
    assert not signature.verify(public, message, b"\x01" * length)


def test_malformed_types_do_not_become_native_pointers():
    seed, public, message, signed = map(bytes.fromhex, VECTORS[0])
    assert not signature.verify(public.hex(), message, signed)
    assert not signature.verify(public, "", signed)
    assert not signature.verify(public, message, None)
    assert not signature.verify(32, message, signed)
    with pytest.raises(ValueError):
        signature.public_key(32)
    with pytest.raises(ValueError):
        signature.sign(seed.hex(), message)
    with pytest.raises(TypeError):
        signature.sign(seed, "manifest")


def test_payload_binary_nuls_and_unicode_are_signed_exactly():
    seed = bytes.fromhex(VECTORS[0][0])
    public = signature.public_key(seed)
    payload = '\x00{"version":"1.1.5.3","notes":"用户版"}\x00'.encode("utf-8")
    signed = signature.sign(seed, payload)
    assert signature.verify(public, payload, signed)
    assert not signature.verify(public, payload.rstrip(b"\x00"), signed)


def test_noncanonical_scalar_is_rejected():
    _, public, message, signed = map(bytes.fromhex, VECTORS[0])
    # Adding the group order to S must not create a second accepted encoding.
    order = 2**252 + 27742317777372353535851937790883648493
    noncanonical = signed[:32] + (int.from_bytes(signed[32:], "little") + order).to_bytes(32, "little")
    assert not signature.verify(public, message, noncanonical)


def test_missing_library_is_an_explicit_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(signature, "_library_candidates", lambda: (tmp_path / "missing.dll",))
    with pytest.raises(signature.SignatureUnavailable, match="OpenSSL"):
        signature._load_library()


def test_unavailable_backend_does_not_look_like_a_valid_update(monkeypatch):
    def unavailable():
        raise signature.SignatureUnavailable("backend unavailable")
    monkeypatch.setattr(signature, "_library", unavailable)
    _, public, message, signed = map(bytes.fromhex, VECTORS[0])
    with pytest.raises(signature.SignatureUnavailable):
        signature.verify(public, message, signed)


def test_library_loader_rejects_relative_test_path():
    with pytest.raises(signature.SignatureUnavailable, match="绝对路径"):
        signature._load_library("libcrypto-3.dll")


def test_frozen_loader_only_uses_bundle_directory(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    monkeypatch.setattr(signature.sys, "frozen", True, raising=False)
    monkeypatch.setattr(signature.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("ARAMHEX_CRYPTO_LIBRARY", str(tmp_path / "untrusted.dll"))
    monkeypatch.chdir(tmp_path)
    candidates = signature._library_candidates()
    assert candidates and all(path.parent == bundle for path in candidates)


def test_frozen_loader_without_bundle_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(signature.sys, "frozen", True, raising=False)
    monkeypatch.delattr(signature.sys, "_MEIPASS", raising=False)
    assert signature._library_candidates() == ()


def test_source_loader_uses_interpreter_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(signature.sys, "frozen", False, raising=False)
    interpreter = tmp_path / "python"
    monkeypatch.setattr(signature.sys, "base_prefix", str(interpreter))
    monkeypatch.setitem(signature.sys.modules, "_ssl", SimpleNamespace(__file__=str(interpreter / "DLLs" / "_ssl.pyd")))
    assert all(path.is_absolute() and path.is_relative_to(interpreter)
               for path in signature._library_candidates())
