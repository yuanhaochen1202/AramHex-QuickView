import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("binary_provenance", Path(__file__).parents[1] / "packaging/binary_provenance.py")
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


def test_rejects_unrelated_path_dependency(tmp_path):
    approved = tmp_path / "venv"
    provenance.check_binary_sources([("Qt6Core.dll", str(approved / "Qt6Core.dll"), "BINARY")], [approved])
    with pytest.raises(RuntimeError, match="Untrusted native"):
        provenance.check_binary_sources([("icuuc.dll", str(tmp_path / "poppler/icuuc.dll"), "BINARY")], [approved])


def test_rejects_shadowing_windows_icu_even_under_allowed_root(tmp_path):
    with pytest.raises(RuntimeError, match="Windows ICU"):
        provenance.check_binary_sources([("icuuc.dll", str(tmp_path / "icuuc.dll"), "BINARY")], [tmp_path])


def test_unrelated_dll_is_rejected_by_provenance_not_only_icu_name(tmp_path):
    with pytest.raises(RuntimeError, match="libcrypto"):
        provenance.check_binary_sources([("libcrypto-3.dll", str(tmp_path / "poppler/libcrypto-3.dll"), "BINARY")], [tmp_path / "venv"])


def test_gui_selftest_precedes_settings_and_engine(tmp_path, monkeypatch):
    from lolhex import __main__, gui_selftest
    monkeypatch.setattr(__main__.Settings, "load", lambda: pytest.fail("settings/data access in GUI smoke"))
    seen = []
    monkeypatch.setattr(gui_selftest, "run", lambda path: seen.append(path) or 0)
    report = str(tmp_path / "gui.json")
    assert __main__.main(["--self-test-gui", report]) == 0
    assert seen == [report]
