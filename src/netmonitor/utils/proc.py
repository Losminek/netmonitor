"""Bezpečné spouštění externích příkazů: vždy seznam argumentů, nikdy shell."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def run_cmd(args: list[str], timeout: float = 3.0, c_locale: bool = True, encoding: str | None = None) -> str | None:
    """Spustí příkaz a vrátí stdout, nebo None při chybě/timeoutu.

    `subprocess.run` při timeoutu proces ukončí (kill) a počká na něj – žádné zombie procesy.
    """
    if not args or shutil.which(args[0]) is None:
        return None
    env = dict(os.environ)
    if c_locale:
        env.update(LC_ALL="C", LANG="C")
    kwargs: dict = dict(capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, env=env, check=False)
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(args, **kwargs)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.debug("Příkaz %s selhal: %s", args[0], e)
        return None
    enc = encoding or ("oem" if sys.platform == "win32" else "utf-8")
    try:
        return p.stdout.decode(enc, errors="replace")
    except LookupError:
        return p.stdout.decode("utf-8", errors="replace")
