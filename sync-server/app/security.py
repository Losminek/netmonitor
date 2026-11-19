"""Hesla (Argon2id), přístupové tokeny (JWT HS256), obnovovací tokeny, licenční klíče a TOTP (RFC 6238)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ISSUER = "netmonitor-sync"
AUDIENCE = "netmonitor-desktop"
_hasher = PasswordHasher()
# Ověřuje se i u neexistujícího účtu, aby odezva neprozradila, zda e-mail existuje.
_DUMMY_HASH = _hasher.hash("netmonitor-dummy-password")
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # bez zaměnitelných znaků 0/O, 1/I


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_refresh_token(session_id) -> str:
    """<id relace>.<tajemství> – podle id server pozná i starý (ukradený) token téže relace."""
    return f"{session_id.hex}.{secrets.token_urlsafe(48)}"


def refresh_session_id(token: str):
    import uuid
    head, sep, _ = token.partition(".")
    if not sep or len(head) != 32:
        return None                       # token z verze 1.0.0
    try:
        return uuid.UUID(hex=head)
    except ValueError:
        return None


def new_license_key() -> str:
    """NM-XXXXX-XXXXX-XXXXX-XXXXX (100 bitů náhody)."""
    groups = ["".join(secrets.choice(_KEY_ALPHABET) for _ in range(5)) for _ in range(4)]
    return "NM-" + "-".join(groups)


def normalize_license_key(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum() or ch == "-").strip("-")


def make_access_token(secret: str, user_id, session_id, mfa: bool, minutes: int) -> str:
    issued = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": str(user_id), "sid": str(session_id), "mfa": bool(mfa),
        "iss": ISSUER, "aud": AUDIENCE, "iat": issued, "exp": issued + timedelta(minutes=minutes),
    }, secret, algorithm="HS256")


def read_access_token(secret: str, token: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE,
                      options={"require": ["exp", "sub", "sid", "iss", "aud"]})


# ---------------------------------------------------------------- TOTP
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def totp_code(secret: str, step: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def totp_verify(secret: str, code: str, last_step: int = 0, at: float | None = None) -> int | None:
    """Vrátí časový krok platného kódu (±1 krok = ±30 s), jinak None. Každý krok lze použít jen jednou."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return None
    current = int((time.time() if at is None else at) // 30)
    for step in (current, current - 1, current + 1):
        if step > last_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def totp_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/NetMonitor:{quote(email)}?secret={secret}&issuer=NetMonitor&digits=6&period=30"


# ---------------------------------------------------------------- šifrování tajemství TOTP v databázi
_SEALED = "enc1:"


def _fernet(server_secret: str):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"netmonitor-sync", info=b"totp-secret-v1"
               ).derive(server_secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def seal(server_secret: str, value: str) -> str:
    return _SEALED + _fernet(server_secret).encrypt(value.encode("ascii")).decode("ascii")


def unseal(server_secret: str, value: str | None) -> str | None:
    """Vrátí tajemství v čitelné podobě. Hodnoty z 1.0.0 (nešifrované) projdou beze změny."""
    if not value or not value.startswith(_SEALED):
        return value
    from cryptography.fernet import InvalidToken
    try:
        return _fernet(server_secret).decrypt(value[len(_SEALED):].encode("ascii")).decode("ascii")
    except InvalidToken as exc:
        raise RuntimeError("Tajemství TOTP nelze dešifrovat – změnil se JWT_SECRET? "
                           "Pomůže `python -m app.cli reset-totp <e-mail>`.") from exc


def is_sealed(value: str | None) -> bool:
    return bool(value) and value.startswith(_SEALED)


def totp_qr_terminal(uri: str) -> None:
    import segno
    segno.make(uri, error="m").terminal(compact=True)
