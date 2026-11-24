"""Správa z příkazové řádky uvnitř kontejneru (docker compose exec api python -m app.cli …).

    create-admin                 první/další účet správce (heslo se zadává skrytě), hned zapne 2FA
    totp-setup <e-mail>          zapnutí dvoufázového ověření (QR kód v terminálu)
    create-key "poznámka" [--uses N] [--days N]
    list-users
    reset-password <e-mail>
    reset-totp <e-mail>          ztracený telefon – vypne 2FA a hned nabídne nové nastavení
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from . import security, settings as settings_mod
from .auth import EMAIL, MIN_PASSWORD
from .main import make_engine, prepare_database
from .models import LicenseKey, User, UserSession, now


def ask_password() -> str:
    first = getpass.getpass(f"Heslo (alespoň {MIN_PASSWORD} znaků): ")
    if len(first) < MIN_PASSWORD:
        raise SystemExit("Heslo je příliš krátké.")
    if getpass.getpass("Heslo znovu: ") != first:
        raise SystemExit("Hesla se neshodují.")
    return first


def find(db, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().casefold()))
    if user is None:
        raise SystemExit("Účet nenalezen.")
    return user


def revoke_sessions(db, user: User) -> None:
    db.execute(update(UserSession).where(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=now()))


def disable_totp(user: User) -> None:
    user.totp_enabled, user.totp_secret, user.totp_last_step = False, None, 0


def totp_setup(db, user: User, server_secret: str, attempts: int = 3) -> bool:
    """Interaktivní zapnutí 2FA: QR kód v terminálu, potvrzení kódem. Odhlásí všechna zařízení."""
    secret = security.new_totp_secret()
    uri = security.totp_uri(secret, user.email)
    print("\nNaskenujte QR kód autentikátorem (Aegis, Google/Microsoft Authenticator, Bitwarden…):\n")
    try:
        security.totp_qr_terminal(uri)
    except Exception:  # noqa: BLE001 – terminál bez podpory; zbývá ruční zadání
        pass
    print("Ruční zadání klíče:", " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "\n")
    for _ in range(attempts):
        step = security.totp_verify(secret, input("Kód z autentikátoru: "), 0)
        if step is not None:
            user.totp_secret = security.seal(server_secret, secret)
            user.totp_enabled, user.totp_last_step = True, step
            revoke_sessions(db, user)
            db.commit()
            print("Dvoufázové ověření je zapnuté. V aplikaci se přihlaste znovu, tentokrát i s kódem.")
            return True
        print("Kód nesouhlasí – zkontrolujte čas v telefonu a zadejte další kód.")
    db.rollback()
    print("Dvoufázové ověření NEBYLO zapnuto. Zopakujte: python -m app.cli totp-setup", user.email)
    return False


def main(argv=None, db_factory=None, server_secret: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-admin")
    key = sub.add_parser("create-key")
    key.add_argument("note")
    key.add_argument("--uses", type=int, default=1)
    key.add_argument("--days", type=int)
    sub.add_parser("list-users")
    for name in ("reset-password", "reset-totp", "totp-setup"):
        sub.add_parser(name).add_argument("email")
    args = parser.parse_args(argv)

    if db_factory is None:
        settings = settings_mod.load()
        engine = make_engine(settings.database_url)
        prepare_database(engine, settings)
        db_factory = sessionmaker(engine, expire_on_commit=False)
        server_secret = settings.seal_key
    with db_factory() as db:
        if args.cmd == "create-admin":
            email = input("E-mail správce: ").strip().casefold()
            if not EMAIL.fullmatch(email):
                raise SystemExit("Neplatný e-mail.")
            user = db.scalar(select(User).where(User.email == email))
            if user is not None:
                if input("Účet existuje. Povýšit na správce? [a/N] ").strip().lower() != "a":
                    return 1
                # 2FA zapnuté dřív přes web mohl nastavit kdokoli s heslem – správce si ho nastaví znovu.
                user.is_admin = True
                disable_totp(user)
                revoke_sessions(db, user)
            else:
                user = User(email=email, password_hash=security.hash_password(ask_password()), is_admin=True)
                db.add(user)
            db.commit()
            print("Účet správce je připraven. Teď zapněte dvoufázové ověření (bez něj správa nefunguje).")
            totp_setup(db, user, server_secret)
        elif args.cmd == "totp-setup":
            user = find(db, args.email)
            if user.totp_enabled and input("2FA už je zapnuté. Nastavit znovu (starý kód přestane platit)? [a/N] "
                                           ).strip().lower() != "a":
                return 1
            return 0 if totp_setup(db, user, server_secret) else 1
        elif args.cmd == "create-key":
            plain = security.new_license_key()
            db.add(LicenseKey(key_hash=security.sha256(plain), prefix=plain[:8], note=args.note.strip()[:200],
                              max_uses=max(1, args.uses),
                              expires_at=now() + timedelta(days=args.days) if args.days else None))
            db.commit()
            print(plain)
        elif args.cmd == "list-users":
            for u in db.scalars(select(User).order_by(User.created_at)).all():
                flags = ("správce " if u.is_admin else "") + ("2FA " if u.totp_enabled else "") + \
                        ("" if u.is_active else "ZABLOKOVÁN")
                print(f"{u.email}\t{flags.strip()}")
        elif args.cmd == "reset-password":
            user = find(db, args.email)
            user.password_hash = security.hash_password(ask_password())
            revoke_sessions(db, user)
            db.commit()
            print("Heslo změněno, všechna zařízení odhlášena.")
        elif args.cmd == "reset-totp":
            user = find(db, args.email)
            disable_totp(user)
            revoke_sessions(db, user)
            db.commit()
            print("Dvoufázové ověření vypnuto, všechna zařízení odhlášena.")
            if user.is_admin and input("Nastavit nové 2FA hned teď? [A/n] ").strip().lower() != "n":
                totp_setup(db, user, server_secret)
    return 0


if __name__ == "__main__":
    sys.exit(main())
