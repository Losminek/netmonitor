"""Windows PowerShell 5.1 čte .ps1 bez BOM jako ANSI => české znaky / pomlčky rozbijí syntaxi."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_windows_text_files_have_utf8_bom():
    files = list((ROOT / "scripts").glob("*.ps1")) + list((ROOT / "packaging" / "windows").glob("*.iss"))
    assert files
    for f in files:
        data = f.read_bytes()
        if any(b > 127 for b in data):
            assert data.startswith(b"\xef\xbb\xbf"), f"{f.name}: chybí UTF-8 BOM"
