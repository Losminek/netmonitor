"""Vstupní skript pro PyInstaller / Nuitka."""
import sys

from netmonitor.main import main

if __name__ == "__main__":
    sys.exit(main())
