import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Každý test má vlastní konfigurační/datový adresář – nikdy nesahá na skutečná data."""
    monkeypatch.setenv("NETMONITOR_HOME", str(tmp_path / "nmhome"))
    yield


@pytest.fixture(autouse=True)
def _no_network_changes(monkeypatch):
    """Pojistka AC-005: skutečné zápisy sítě nesmí spustit žádný test sady."""
    from netmonitor.platform import linux, macos, windows

    def forbidden(*args, **kwargs):
        raise AssertionError("Test se pokusil skutečně změnit nastavení sítě")

    for module in (linux, macos, windows):
        monkeypatch.setattr(module, "apply_ip", forbidden)
    popen = subprocess.Popen

    def guarded_popen(args, *positional, **kwargs):
        parts = shlex.split(args) if isinstance(args, str) else [os.fsdecode(a) for a in args]
        names = [Path(a).name.lower() for a in parts]
        if any(n in ("pkexec", "netsh", "netsh.exe", "networksetup") for n in names):
            forbidden()
        if "nmcli" in names and any(n in ("modify", "up") for n in names):
            forbidden()
        if any("with administrator privileges" in a or "ShellExecute" in a for a in parts):
            forbidden()
        return popen(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
