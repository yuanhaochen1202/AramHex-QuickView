"""Removed diagnostic uploads: inert compatibility module for old frozen bundles.

The implementation intentionally has no file access, network calls, background
threads or remote logging controls. Keeping this module replaces the old bundled
implementation when rebuilding the existing executable.
"""


def maybe_upload(settings, cfg: dict, timeout: float = 30) -> list[str]:
    return []


def apply_verbose(settings, cfg: dict, now: float | None = None) -> bool:
    # Local debug logging is configured only by __main__._setup_logging.
    return bool(getattr(settings, "debug_mode", False))
