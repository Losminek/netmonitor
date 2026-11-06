import os
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
