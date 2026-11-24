"""Registrace licenčním klíčem, přihlášení, rotace obnovovacích tokenů a dvoufázové ověření."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import security
from .deps import Auth, client_ip, current_auth, error, get_db, limit
from .models import LicenseKey, User, UserSession, aware, now

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MIN_PASSWORD = 10


class RegisterBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    license_key: str = Field(max_length=64)
    device: str = Field(default="", max_length=100)


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    totp_code: str | None = Field(default=None, max_length=12)
    device: str = Field(default="", max_length=100)


class RefreshBody(BaseModel):
    refresh_token: str = Field(max_length=200)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class CodeBody(BaseModel):
    code: str = Field(max_length=12)


class TotpEnableBody(CodeBody):
    # Zapnutí 2FA potvrzuje heslo: samotný (třeba ukradený) token k převzetí účtu nestačí.
    password: str = Field(default="", max_length=200)


def normalize_email(raw: str) -> str:
    email = raw.strip().casefold()
    if len(email) > 254 or not EMAIL.fullmatch(email):
        raise error(422, "invalid_email", "Neplatná e-mailová adresa.")
    return email


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise error(422, "weak_password", f"Heslo musí mít alespoň {MIN_PASSWORD} znaků.")


def user_dict(user: User) -> dict:
    return {"id": str(user.id), "email": user.email, "is_admin": user.is_admin,
            "totp_enabled": user.totp_enabled}


def open_session(request: Request, db: Session, user: User, device: str, mfa: bool) -> dict:
    settings = request.app.state.settings
    # Omezení počtu zařízení: nejstarší relace se ukončí.
    active = db.scalars(select(UserSession).where(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
    ).order_by(UserSession.last_used_at.desc())).all()
    for old in active[settings.max_sessions - 1:]:
        old.revoked_at = now()
    session_id = uuid.uuid4()
    token = security.new_refresh_token(session_id, settings.jwt_secret)
    session = UserSession(id=session_id, user_id=user.id, token_hash=security.sha256(token), device=device.strip()[:100],
                          mfa=mfa, mfa_at=now() if mfa else None, ip=client_ip(request)[:45],
                          expires_at=now() + timedelta(days=settings.refresh_days))
    db.add(session)
    purge_old_sessions(db)
    user.last_login_at = now()
    db.commit()
    return token_response(request, user, session, token)


def purge_old_sessions(db: Session) -> None:
    """Ukončené a vypršelé relace (i s IP adresou) se po 30 dnech mažou."""
    cutoff = now() - timedelta(days=30)
    db.execute(delete(UserSession).where((UserSession.revoked_at < cutoff) | (UserSession.expires_at < cutoff))
               .execution_options(synchronize_session=False))


def token_response(request: Request, user: User, session: UserSession, refresh_token: str) -> dict:
    settings = request.app.state.settings
    return {
        "access_token": security.make_access_token(settings.jwt_secret, user.id, session.id, session.mfa,
                                                   settings.access_minutes),
        "refresh_token": refresh_token, "token_type": "bearer",
        "expires_in": settings.access_minutes * 60, "mfa": bool(session.mfa), "user": user_dict(user),
    }


@router.post("/register", status_code=201)
def register(body: RegisterBody, request: Request, db: Session = Depends(get_db)):
    settings = request.app.state.settings
    limit(request, "register", settings.rate_register)
    if not settings.registration_enabled:
        raise error(403, "registration_disabled", "Registrace je na tomto serveru vypnutá.")
    email = normalize_email(body.email)
    check_password(body.password)
    key = db.scalar(select(LicenseKey).where(
        LicenseKey.key_hash == security.sha256(security.normalize_license_key(body.license_key))))
    if (key is None or key.revoked_at is not None or key.uses >= key.max_uses
            or (key.expires_at is not None and aware(key.expires_at) <= now())):
        raise error(403, "invalid_license_key", "Licenční klíč je neplatný, vyčerpaný nebo mu skončila platnost.")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise error(409, "email_exists", "Účet s tímto e-mailem už existuje.")
    # Atomické čerpání klíče: dva souběžné požadavky nesmí jeden klíč použít dvakrát.
    taken = db.execute(update(LicenseKey).where(
        LicenseKey.id == key.id, LicenseKey.uses < LicenseKey.max_uses, LicenseKey.revoked_at.is_(None)
    ).values(uses=LicenseKey.uses + 1))
    if taken.rowcount != 1:
        db.rollback()
        raise error(403, "invalid_license_key", "Licenční klíč je neplatný, vyčerpaný nebo mu skončila platnost.")
    user = User(email=email, password_hash=security.hash_password(body.password), license_key_id=key.id)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise error(409, "email_exists", "Účet s tímto e-mailem už existuje.") from exc
    return open_session(request, db, user, body.device, mfa=False)


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    settings = request.app.state.settings
    limit(request, "login", settings.rate_login)
    email = body.email.strip().casefold()
    limiter = request.app.state.limiter
    # Chybné pokusy se počítají na dvojici IP + e-mail. Odpověď je stejná pro existující i neexistující
    # účet a cizí IP adresa nemůže účet zamknout jeho vlastníkovi.
    fail_key = f"fail:{client_ip(request)}:{email}"
    wait = limiter.blocked(fail_key, *settings.login_failures)
    if wait:
        security.verify_password(None, body.password)        # stejná doba odezvy
        raise error(429, "rate_limited", f"Příliš mnoho chybných pokusů. Zkuste to za {wait} s.",
                    {"Retry-After": str(wait)})
    user = db.scalar(select(User).where(User.email == email))
    ok = security.verify_password(user.password_hash if user else None, body.password)
    if user is None or not ok or not user.is_active:
        limiter.hit(fail_key)
        raise error(401, "invalid_credentials", "Nesprávný e-mail nebo heslo.")
    mfa = False
    if user.totp_enabled:
        if not body.totp_code:
            raise error(401, "totp_required", "Zadejte kód z autentikátoru.")
        if not consume_totp(request, db, user, body.totp_code):
            limiter.hit(fail_key)
            raise error(401, "invalid_totp", "Nesprávný nebo již použitý kód z autentikátoru.")
        mfa = True
    limiter.clear(fail_key)
    return open_session(request, db, user, body.device, mfa=mfa)


def consume_totp(request: Request, db: Session, user: User, code: str) -> bool:
    """Ověří kód a atomicky jej spotřebuje – stejný kód nelze použít dvakrát ani souběžně."""
    try:
        secret = security.unseal(request.app.state.settings.seal_key, user.totp_secret)
    except RuntimeError:
        log.error("Tajemství 2FA účtu %s nelze dešifrovat (změněný JWT_SECRET/TOTP_KEY?).", user.id)
        raise error(503, "totp_unavailable", "Dvoufázové ověření teď na serveru nejde ověřit. "
                    "Kontaktujte správce serveru.") from None
    step = security.totp_verify(secret, code, user.totp_last_step) if secret else None
    if step is None:
        return False
    taken = db.execute(update(User).where(User.id == user.id, User.totp_last_step < step)
                       .values(totp_last_step=step))
    db.commit()
    return taken.rowcount == 1


@router.post("/refresh")
def refresh(body: RefreshBody, request: Request, db: Session = Depends(get_db)):
    settings = request.app.state.settings
    limit(request, "refresh", (60, 300))
    digest = security.sha256(body.refresh_token)
    sid = security.refresh_session_id(body.refresh_token)
    if sid is not None:
        session = db.get(UserSession, sid)
    else:                                  # token z 1.0.0 – dohledání podle otisku
        session = db.scalar(select(UserSession).where(
            (UserSession.token_hash == digest) | (UserSession.previous_hash == digest)))
    if session is None:
        raise error(401, "unauthorized", "Relace vypršela. Přihlaste se znovu.")
    token = security.new_refresh_token(session.id, settings.jwt_secret)
    valid = (UserSession.id == session.id, UserSession.revoked_at.is_(None), UserSession.expires_at > now())
    # Atomická výměna: stejný token obnoví relaci nejvýš jednou.
    rotated = db.execute(update(UserSession).where(UserSession.token_hash == digest, *valid).values(
        token_hash=security.sha256(token), previous_hash=digest, rotated_at=now(), last_used_at=now(),
        ip=client_ip(request)[:45],
        expires_at=now() + timedelta(days=settings.refresh_days)
    ).execution_options(synchronize_session=False))
    if rotated.rowcount != 1:
        # Klient mohl o odpověď přijít (uspání, výpadek Wi-Fi) a zkouší předchozí token znovu.
        grace = now() - timedelta(seconds=settings.refresh_grace_s)
        # Tolerance platí jen jednou: previous_hash se smaže, další opakování už je krádež.
        rotated = db.execute(update(UserSession).where(
            UserSession.previous_hash == digest, UserSession.rotated_at > grace, *valid
        ).values(token_hash=security.sha256(token), previous_hash=None, last_used_at=now(),
                 ip=client_ip(request)[:45]).execution_options(synchronize_session=False))
    if rotated.rowcount != 1:
        issued = (sid is None or digest == session.previous_hash
                  or security.refresh_token_issued(settings.jwt_secret, body.refresh_token))
        if not issued:
            # Token, který server nevydal (jen se zná id relace) – relaci neukončí.
            db.rollback()
            raise error(401, "unauthorized", "Relace vypršela. Přihlaste se znovu.")
        # Starší token téže relace = token byl zkopírován. Relace končí pro všechny jeho držitele.
        db.execute(update(UserSession).where(UserSession.id == session.id, UserSession.revoked_at.is_(None))
                   .values(revoked_at=now()).execution_options(synchronize_session=False))
        db.commit()
        raise error(401, "unauthorized", "Relace vypršela. Přihlaste se znovu.")
    db.commit()
    db.refresh(session)
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise error(401, "unauthorized", "Relace vypršela. Přihlaste se znovu.")
    return token_response(request, user, session, token)


@router.post("/logout", status_code=204)
def logout(auth: Auth = Depends(current_auth), db: Session = Depends(get_db)):
    auth.session.revoked_at = now()
    db.commit()


@router.get("/me")
def me(auth: Auth = Depends(current_auth)):
    return {**user_dict(auth.user), "mfa": auth.mfa}


@router.post("/password", status_code=204)
def change_password(body: PasswordBody, request: Request, auth: Auth = Depends(current_auth),
                    db: Session = Depends(get_db)):
    limit(request, "login", request.app.state.settings.rate_login)
    if not security.verify_password(auth.user.password_hash, body.current_password):
        raise error(401, "invalid_credentials", "Současné heslo nesouhlasí.")
    check_password(body.new_password)
    auth.user.password_hash = security.hash_password(body.new_password)
    # Ostatní zařízení se musí přihlásit znovu.
    db.execute(update(UserSession).where(
        UserSession.user_id == auth.user.id, UserSession.id != auth.session.id,
        UserSession.revoked_at.is_(None)).values(revoked_at=now()))
    db.commit()


def _no_web_enrollment_for_admin(user: User) -> None:
    # Kdo zná heslo správce, nesmí si sám zapnout 2FA a tím získat správu. Správce jej zapíná na serveru.
    if user.is_admin:
        raise error(403, "totp_cli_required", "Dvoufázové ověření správce se zapíná na serveru příkazem: "
                    "python -m app.cli totp-setup <e-mail>")


@router.post("/totp/setup")
def totp_setup(request: Request, auth: Auth = Depends(current_auth), db: Session = Depends(get_db)):
    _no_web_enrollment_for_admin(auth.user)
    if auth.user.totp_enabled:
        raise error(409, "totp_already_enabled", "Dvoufázové ověření je už zapnuté.")
    secret = security.new_totp_secret()
    auth.user.totp_secret = security.seal(request.app.state.settings.seal_key, secret)
    db.commit()
    return {"secret": secret, "uri": security.totp_uri(secret, auth.user.email)}


@router.post("/totp/enable")
def totp_enable(body: TotpEnableBody, request: Request, auth: Auth = Depends(current_auth),
                db: Session = Depends(get_db)):
    limit(request, "login", request.app.state.settings.rate_login)
    user = auth.user
    _no_web_enrollment_for_admin(user)
    if not security.verify_password(user.password_hash, body.password):
        raise error(401, "invalid_credentials", "Současné heslo nesouhlasí.")
    if user.totp_enabled:
        raise error(409, "totp_already_enabled", "Dvoufázové ověření je už zapnuté.")
    if not user.totp_secret:
        raise error(409, "totp_not_started", "Nejprve spusťte nastavení dvoufázového ověření.")
    if not consume_totp(request, db, user, body.code):
        raise error(401, "invalid_totp", "Kód nesouhlasí. Zkontrolujte čas v telefonu a zkuste další kód.")
    user.totp_enabled = True
    # Všechny dosavadní relace (i tato) končí; pokračuje se novou. Token zkopírovaný před zapnutím
    # 2FA tak neplatí.
    db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
               .values(revoked_at=now()))
    db.commit()
    return open_session(request, db, user, auth.session.device, mfa=True)
