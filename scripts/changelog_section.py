"""Vypíše poznámky k vydání dané verze z CHANGELOG.md:  python scripts/changelog_section.py 1.2.0"""
import re
import sys
from pathlib import Path

text = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## {re.escape(sys.argv[1])}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
print(m.group(1).strip() if m else f"NetMonitor {sys.argv[1]}")
