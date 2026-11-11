"""Reject DLLs resolved from unrelated tools on the build machine's PATH."""
from pathlib import Path


def check_binary_sources(entries, allowed_roots):
    roots = [Path(root).resolve() for root in allowed_roots]
    rejected = []
    for destination, source, kind in entries:
        path = Path(source).resolve()
        if not any(path.is_relative_to(root) for root in roots):
            rejected.append(f"{destination}: {path}")
        # Qt uses Windows ICU with unversioned exports. A third-party icuuc.dll
        # (e.g. Poppler ICU78) shadows that API even when its filename matches.
        if Path(destination).name.lower() in {"icuuc.dll", "icuin.dll", "icudt78.dll"}:
            rejected.append(f"Windows ICU must not be bundled: {destination}")
    if rejected:
        raise RuntimeError("Untrusted native dependency sources:\n" + "\n".join(rejected))
