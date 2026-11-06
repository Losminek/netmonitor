"""Umístění konfigurace, dat a logů podle konvencí OS."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "NetMonitor"


def _env_override() -> Path | None:
    p = os.environ.get("NETMONITOR_HOME")
    return Path(p) if p else None


def config_dir() -> Path:
    if (o := _env_override()):
        return o
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "netmonitor"


def data_dir() -> Path:
    if (o := _env_override()):
        return o
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "netmonitor"


def log_dir() -> Path:
    if sys.platform == "darwin" and not _env_override():
        return Path.home() / "Library" / "Logs" / APP_DIR_NAME
    return data_dir() / "logs"


def resources_dir() -> Path:
    """Funguje i v PyInstaller balíčku (sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "netmonitor" / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def ensure_dirs() -> None:
    for d in (config_dir(), data_dir(), log_dir()):
        d.mkdir(parents=True, exist_ok=True)
