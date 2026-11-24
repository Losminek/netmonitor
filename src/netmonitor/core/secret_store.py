"""Uložení obnovovacího tokenu synchronizace na tomto počítači.

Windows: šifrováno DPAPI pro přihlášeného uživatele (CryptProtectData).
Linux/macOS: soubor s právy 0600 v konfiguračním adresáři uživatele.
Heslo se neukládá nikdy.
"""
from __future__ import annotations

import base64
import json
import logging
import sys
from pathlib import Path

from .config import atomic_write_json
from ..utils.paths import config_dir

log = logging.getLogger(__name__)

FILE = "sync-session.json"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)):   # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("DPAPI selhalo")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


class SecretStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / FILE

    def save(self, server: str, email: str, refresh_token: str) -> None:
        # Odstranit i případný token z nedokončeného zápisu starší verze.
        try:
            self.path.with_suffix(".tmp").unlink(missing_ok=True)
        except OSError:
            log.warning("Nepodařilo se odstranit pozůstatek přihlašovací relace.")
        raw = refresh_token.encode("utf-8")
        if sys.platform == "win32":
            payload = {"scheme": "dpapi", "token": base64.b64encode(_dpapi(raw, True)).decode("ascii")}
        else:
            payload = {"scheme": "file", "token": refresh_token}
        payload.update(server=server, email=email)
        # mkstemp v pomocníku vytváří unikátní soubor s právy 0600 už od vzniku.
        atomic_write_json(self.path, payload)

    def load(self) -> tuple[str, str, str] | None:
        """(server, e-mail, obnovovací token), nebo None. Poškozený soubor se tiše ignoruje."""
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            token = data["token"]
            if data.get("scheme") == "dpapi":
                token = _dpapi(base64.b64decode(token), False).decode("utf-8")
            if not (isinstance(token, str) and token and isinstance(data["server"], str)
                    and isinstance(data["email"], str)):
                return None
            return data["server"], data["email"], token
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
        try:
            self.path.with_suffix(".tmp").unlink(missing_ok=True)
        except OSError:
            pass
