"""Release artifacts must retain readable license texts with their original bytes."""
import importlib.util
from pathlib import Path
import re
import zipfile

import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("aramhex_license_bundle", ROOT / "packaging/license_bundle.py")
licenses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(licenses)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for name in licenses.PROJECT_LICENSES:
        (root / name).write_bytes((name + " original text\r\n").encode("utf-8"))
    nested = root / "third_party_licenses/qt"
    nested.mkdir(parents=True)
    (nested / "LICENSE").write_bytes(b"original third-party license\r\n")
    (nested / "COPYRIGHT.txt").write_bytes(b"original attribution\n")
    return root


def test_portable_layout_keeps_all_notices_and_exact_license_bytes(source, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "AramHex.exe").write_bytes(b"MZ-test")
    assert licenses.install_release_licenses(source, dist) == 6
    package = tmp_path / "portable.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for path in dist.rglob("*"):
            if path.is_file():
                archive.write(path, "AramHex/" + path.relative_to(dist).as_posix())
    with zipfile.ZipFile(package) as archive:
        for original, installed in licenses.release_license_files(source):
            assert archive.read("AramHex/" + installed.as_posix()) == original.read_bytes()


@pytest.mark.parametrize("missing", [*licenses.PROJECT_LICENSES, "third_party_licenses"])
def test_missing_license_material_blocks_packaging_before_partial_copy(source, tmp_path, missing):
    target = source / missing
    if target.is_dir():
        target.rename(source / "withheld-licenses")
    else:
        target.unlink()
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(RuntimeError):
        licenses.install_release_licenses(source, dist)
    assert not list(dist.iterdir())


def test_direct_pyinstaller_data_retains_nested_third_party_notices(source):
    datas = licenses.pyinstaller_license_datas(source)
    destinations = {Path(original).name + ":" + destination for original, destination in datas}
    assert all(name + ":RELEASE-LICENSES" in destinations for name in licenses.PROJECT_LICENSES)
    assert "LICENSE:THIRD-PARTY-LICENSES/qt" in destinations
    assert "COPYRIGHT.txt:THIRD-PARTY-LICENSES/qt" in destinations


def test_installer_root_entries_do_not_exclude_nested_dependency_licenses():
    text = (ROOT / "packaging/LolHex.iss").read_text(encoding="utf-8-sig")
    section = text.split("[Files]", 1)[1].split("[", 1)[0]
    entries = [line for line in section.splitlines() if line.startswith("Source:")]
    for name in licenses.PROJECT_LICENSES:
        assert any(f'AramHex\\{name}";' in line and 'DestDir: "{app}"' in line for line in entries)
    wildcard = next(line for line in entries if 'AramHex\\*"' in line)
    patterns = re.search(r'Excludes: "([^"]+)"', wildcard).group(1).split(",")
    # Inno patterns without a leading slash would also remove nested LICENSE files.
    assert set(patterns) == {"\\" + name for name in licenses.PROJECT_LICENSES}
    assert "recursesubdirs" in wildcard
    assert "AppSupportURL=" not in text


def test_official_build_verifies_licenses_before_both_artifact_types():
    text = (ROOT / "packaging/build.ps1").read_text(encoding="utf-8-sig")
    include = text.index("packaging\\license_bundle.py")
    assert include < text.index('& $iscc "/DAppVersion=')
    assert include < text.index("Compress-Archive")
    assert "pyinstaller_license_datas(Path(SPEC).parent.parent)" in (
        ROOT / "packaging/lolhex.spec").read_text(encoding="utf-8-sig")
