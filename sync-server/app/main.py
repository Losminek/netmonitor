from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from . import __version__, admin, auth, profiles, settings as settings_mod
from .deps import client_ip
from .models import Base, User, upgrade_schema
from .ratelimit import RateLimiter

log = logging.getLogger("netmonitor-sync")
MAX_BODY = 700 * 1024


def make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


def prepare_database(engine, settings) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from . import security
    Base.metadata.create_all(engine)
    upgrade_schema(engine)
    with Session(engine) as db:          # tajemství TOTP z 1.0.0 se zašifrují
        for user in db.scalars(select(User).where(User.totp_secret.is_not(None))).all():
            if not security.is_sealed(user.totp_secret):
                user.totp_secret = security.seal(settings.seal_key, user.totp_secret)
        db.commit()


def create_app(settings: settings_mod.Settings | None = None) -> FastAPI:
    settings = settings or settings_mod.load()
    engine = make_engine(settings.database_url)
    prepare_database(engine, settings)
    # Veřejná dokumentace API je vypnutá – server nemá co prozrazovat.
    app = FastAPI(title="NetMonitor Sync", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.limiter = RateLimiter()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > MAX_BODY:
            return JSONResponse({"detail": {"code": "too_large", "message": "Požadavek je příliš velký."}}, 413)
        if request.method in ("POST", "PUT", "PATCH") and "content-length" not in request.headers:
            return JSONResponse({"detail": {"code": "length_required", "message": "Chybí délka požadavku."}}, 411)
        if request.url.path != "/health":
            limit, window = settings.rate_general
            wait = app.state.limiter.check("all:" + client_ip(request), limit, window)
            if wait:
                return JSONResponse({"detail": {"code": "rate_limited",
                                                "message": f"Příliš mnoho požadavků. Zkuste to za {wait} s."}},
                                    429, headers={"Retry-After": str(wait)})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__,
                "registration": "license_key" if settings.registration_enabled else "disabled"}

    app.include_router(auth.router)
    app.include_router(profiles.router)
    app.include_router(admin.router)
    return app
