"""Carry the reviewed license texts into every distributable without rewriting them."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil


PROJECT_LICENSES = ("LICENSE", "NOTICE.md", "PRIVACY.md", "DATA-LICENSE.txt")


def release_license_files(root: Path) -> list[tuple[Path, Path]]:
    """Return source files and their paths relative to the installed application."""
    root = Path(root).resolve()
    result = []
    for name in PROJECT_LICENSES:
        source = root / name
        if not source.is_file() or source.is_symlink() or source.stat().st_size == 0:
            raise RuntimeError(f"Missing or invalid release license: {name}")
        result.append((source, Path(name)))
    third_party = root / "third_party_licenses"
    if not third_party.is_dir() or third_party.is_symlink():
        raise RuntimeError("Missing reviewed third_party_licenses directory")
    sources = sorted(third_party.rglob("*"))
    if any(path.is_symlink() for path in sources):
        raise RuntimeError("Third-party license files must not contain symbolic links")
    sources = [path for path in sources if path.is_file()]
    if not sources or any(path.stat().st_size == 0 for path in sources):
        raise RuntimeError("Third-party license bundle is empty or contains an empty file")
    for source in sources:
        result.append((source, Path("_internal/THIRD-PARTY-LICENSES") / source.relative_to(third_party)))
    return result


def pyinstaller_license_datas(root: Path) -> list[tuple[str, str]]:
    """PyInstaller puts data under _internal; root copies are added by build.ps1."""
    result = []
    for source, installed in release_license_files(root):
        if installed.parts[0] == "_internal":
            destination = Path(*installed.parts[1:]).parent.as_posix()
        else:
            destination = "RELEASE-LICENSES"
        result.append((str(source), destination))
    return result


def install_release_licenses(root: Path, dist: Path) -> int:
    """Run before both installer and ZIP creation; preserve the exact source bytes."""
    dist = Path(dist).resolve()
    if not dist.is_dir():
        raise RuntimeError("The built application directory does not exist")
    files = release_license_files(root)
    for source, relative in files:
        target = dist / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
            raise RuntimeError(f"Release license copy verification failed: {relative}")
    return len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Include reviewed license texts in the build")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--dist", required=True, type=Path)
    args = parser.parse_args(argv)
    print(f"Release license files copied and verified: {install_release_licenses(args.root, args.dist)}")


if __name__ == "__main__":
    main()
