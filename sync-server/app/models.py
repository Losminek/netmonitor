from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    """SQLite vrací čas bez zóny; vše ukládáme v UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class Base(DeclarativeBase):
    pass


class LicenseKey(Base):
    __tablename__ = "license_keys"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)      # SHA-256, klíč sám se neukládá
    prefix: Mapped[str] = mapped_column(String(12))                     # začátek klíče pro orientaci správce
    note: Mapped[str] = mapped_column(String(200), default="")         # pro koho klíč je
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)   # šifrováno (security.seal)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)     # ochrana proti opakování kódu
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)          # nepoužívá se (od 1.1.0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # nepoužívá se
    license_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("license_keys.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSession(Base):
    """Obnovovací token jednoho zařízení. Při každém obnovení se vymění (rotace)."""
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device: Mapped[str] = mapped_column(String(100), default="")
    mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # poslední ověřený kód
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("user_id", "name_key", name="uq_profile_user_name"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(100))                  # casefold pro jedinečnost
    config: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def upgrade_schema(engine) -> None:
    """Doplní sloupce přidané v novějších verzích (create_all existující tabulky nemění).

    Všechny nové sloupce jsou NULL-ovatelné, takže stačí prosté ADD COLUMN.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in present:
                    kind = column.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}'))
        if engine.dialect.name == "postgresql":
            # 1.0.0 mělo pro tajemství TOTP jen 64 znaků; zašifrované je delší.
            conn.execute(text("ALTER TABLE users ALTER COLUMN totp_secret TYPE VARCHAR(255)"))
