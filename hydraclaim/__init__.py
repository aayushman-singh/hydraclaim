"""HydraClaim — conflict-aware temporal agent memory on HydraDB.

Hack Hydra 2026, Track 3 (memory and context retrieval). See PLAN.md.
"""

from __future__ import annotations

import sys


def _force_utf8_io() -> None:
    """Emit stdout/stderr as UTF-8, including when redirected.

    Windows Python defaults redirected output to the ANSI codepage
    (e.g. cp1252), which corrupts the em-dashes used in answers. The demo
    capture pipeline writes CLI output to text files, so the bytes must be
    UTF-8. This is a no-op when the stream is already UTF-8 or is not a
    real text stream (e.g. pytest's capture).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


_force_utf8_io()
