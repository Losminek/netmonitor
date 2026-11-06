"""Platformní adaptéry. `current()` vrací modul pro aktuální OS."""
from __future__ import annotations

import os
import sys


def current():
    if sys.platform == "win32":
        from . import windows as mod
    elif sys.platform == "darwin":
        from . import macos as mod
    else:
        from . import linux as mod
    return mod


def session_type() -> str:
    """'windows' | 'macos' | 'x11' | 'wayland' | 'unknown'"""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    st = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if st in ("wayland", "x11"):
        return st
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"
