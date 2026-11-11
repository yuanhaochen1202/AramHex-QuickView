"""Ed25519 signatures for custom releases, using the bundled OpenSSL 3 library.

The application only needs ``verify`` and a compiled-in public key. ``sign`` and
``public_key`` are also used by the local release builder; this module neither
creates nor stores private keys. No key or native-library location is taken from
an update response, a settings file, PATH, or an environment-variable override.

Ed25519 signs the exact payload bytes (PureEdDSA), with no external digest:
https://docs.openssl.org/3.0/man7/EVP_SIGNATURE-ED25519/
"""

from __future__ import annotations

import ctypes
import sys
from functools import lru_cache
from pathlib import Path


KEY_BYTES = 32
SIGNATURE_BYTES = 64
_LIBRARY_NAMES = ("libcrypto-3.dll", "libcrypto-3-x64.dll")


class SignatureUnavailable(RuntimeError):
    """The trusted OpenSSL Ed25519 implementation could not be used."""


def _library_candidates() -> tuple[Path, ...]:
    """Only explicit directories belonging to this application/interpreter."""
    if getattr(sys, "frozen", False):
        # A frozen app must use its own runtime, never a separately installed
        # Python or a DLL supplied by the current directory/search path.
        value = getattr(sys, "_MEIPASS", None)
        roots = [Path(value)] if value else []
    else:
        roots = []
        try:
            import _ssl

            if getattr(_ssl, "__file__", None):
                roots.append(Path(_ssl.__file__).parent)
        except ImportError:
            pass
        roots.extend((Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs"))
    paths = []
    for root in roots:
        if root.is_absolute():
            for name in _LIBRARY_NAMES:
                path = root / name
                if path not in paths:
                    paths.append(path)
    return tuple(paths)


def _configure_library(lib):
    """Declare every ABI type, including 64-bit pointers and size_t values."""
    pointer, size, integer = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int
    for name in ("EVP_PKEY_new_raw_public_key_ex", "EVP_PKEY_new_raw_private_key_ex"):
        function = getattr(lib, name)
        function.argtypes = [pointer, ctypes.c_char_p, ctypes.c_char_p, pointer, size]
        function.restype = pointer
    lib.EVP_PKEY_get_raw_public_key.argtypes = [pointer, pointer, ctypes.POINTER(size)]
    lib.EVP_PKEY_get_raw_public_key.restype = integer
    lib.EVP_PKEY_free.argtypes = [pointer]
    lib.EVP_PKEY_free.restype = None
    lib.EVP_MD_CTX_new.argtypes = []
    lib.EVP_MD_CTX_new.restype = pointer
    lib.EVP_MD_CTX_free.argtypes = [pointer]
    lib.EVP_MD_CTX_free.restype = None
    for name in ("EVP_DigestVerifyInit", "EVP_DigestSignInit"):
        function = getattr(lib, name)
        function.argtypes = [pointer, ctypes.POINTER(pointer), pointer, pointer, pointer]
        function.restype = integer
    lib.EVP_DigestVerify.argtypes = [pointer, pointer, size, pointer, size]
    lib.EVP_DigestVerify.restype = integer
    lib.EVP_DigestSign.argtypes = [pointer, pointer, ctypes.POINTER(size), pointer, size]
    lib.EVP_DigestSign.restype = integer
    return lib


def _load_library(libpath: str | Path | None = None):
    """Load an absolute trusted path; the explicit path is for isolated tests.

    Public operations always use the default trusted-directory lookup. There is
    deliberately no environment-variable or current-directory fallback.
    """
    candidates = (Path(libpath),) if libpath is not None else _library_candidates()
    for candidate in candidates:
        if not candidate.is_absolute():
            raise SignatureUnavailable("更新签名库必须使用可信目录中的绝对路径")
        if not candidate.is_file():
            continue
        try:
            # Restrict dependent DLL resolution to this DLL's directory and
            # System32. This is a C ABI (CDLL), not WinDLL's stdcall ABI.
            lib = ctypes.CDLL(str(candidate), winmode=0x00000100 | 0x00000800)
            return _configure_library(lib)
        except (OSError, AttributeError):
            continue
    raise SignatureUnavailable("找不到可用的 OpenSSL 3 更新签名库，已停止验证更新")


@lru_cache(maxsize=1)
def _library():
    return _load_library()


def _buffer(data: bytes):
    # An empty payload is valid in Ed25519; still provide a non-null buffer.
    return ctypes.create_string_buffer(data, max(1, len(data)))


def _private_key(lib, seed: bytes):
    if not isinstance(seed, bytes) or len(seed) != KEY_BYTES:
        raise ValueError("Ed25519 私钥种子必须是 32 字节")
    seed_buffer = _buffer(seed)
    try:
        key = lib.EVP_PKEY_new_raw_private_key_ex(None, b"ED25519", None,
                                                seed_buffer, len(seed))
    finally:
        ctypes.memset(seed_buffer, 0, len(seed))
    if not key:
        raise SignatureUnavailable("OpenSSL 无法创建 Ed25519 签名密钥")
    return key


def public_key(seed: bytes) -> bytes:
    """Derive the raw 32-byte public key from a 32-byte Ed25519 seed."""
    if not isinstance(seed, bytes) or len(seed) != KEY_BYTES:
        raise ValueError("Ed25519 私钥种子必须是 32 字节")
    lib = _library()
    key = _private_key(lib, seed)
    try:
        output = ctypes.create_string_buffer(KEY_BYTES)
        length = ctypes.c_size_t(KEY_BYTES)
        if (lib.EVP_PKEY_get_raw_public_key(key, output, ctypes.byref(length)) != 1
                or length.value != KEY_BYTES):
            raise SignatureUnavailable("OpenSSL 无法导出 Ed25519 公钥")
        return output.raw
    finally:
        lib.EVP_PKEY_free(key)


def sign(seed: bytes, payload: bytes) -> bytes:
    """Sign exact bytes locally; no files, settings, or network are accessed."""
    if not isinstance(seed, bytes) or len(seed) != KEY_BYTES:
        raise ValueError("Ed25519 私钥种子必须是 32 字节")
    if not isinstance(payload, bytes):
        raise TypeError("更新签名内容必须是字节串")
    lib = _library()
    key = _private_key(lib, seed)
    context = None
    try:
        context = lib.EVP_MD_CTX_new()
        if not context or lib.EVP_DigestSignInit(context, None, None, None, key) != 1:
            raise SignatureUnavailable("OpenSSL 无法初始化 Ed25519 签名")
        output = ctypes.create_string_buffer(SIGNATURE_BYTES)
        length = ctypes.c_size_t(SIGNATURE_BYTES)
        if (lib.EVP_DigestSign(context, output, ctypes.byref(length),
                              _buffer(payload), len(payload)) != 1
                or length.value != SIGNATURE_BYTES):
            raise SignatureUnavailable("OpenSSL 无法生成 Ed25519 签名")
        return output.raw
    finally:
        if context:
            lib.EVP_MD_CTX_free(context)
        lib.EVP_PKEY_free(key)


def verify(public: bytes, payload: bytes, signature: bytes) -> bool:
    """Return False for malformed/invalid signatures; fail closed if unavailable."""
    if (not isinstance(public, bytes) or len(public) != KEY_BYTES
            or not isinstance(payload, bytes)
            or not isinstance(signature, bytes) or len(signature) != SIGNATURE_BYTES):
        return False
    lib = _library()
    key = lib.EVP_PKEY_new_raw_public_key_ex(None, b"ED25519", None,
                                           _buffer(public), len(public))
    if not key:
        raise SignatureUnavailable("OpenSSL 无法创建 Ed25519 验签密钥")
    context = None
    try:
        context = lib.EVP_MD_CTX_new()
        if not context or lib.EVP_DigestVerifyInit(context, None, None, None, key) != 1:
            raise SignatureUnavailable("OpenSSL 无法初始化 Ed25519 验签")
        return lib.EVP_DigestVerify(context, _buffer(signature), len(signature),
                                    _buffer(payload), len(payload)) == 1
    finally:
        if context:
            lib.EVP_MD_CTX_free(context)
        lib.EVP_PKEY_free(key)
