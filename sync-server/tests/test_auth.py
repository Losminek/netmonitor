from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import security
from app.main import create_app
from app.models import LicenseKey, User, now
from sqlalchemy import select  # noqa: F811

from .conftest import PASSWORD, add_user, bearer, code, login, make_settings, new_key, register


def test_health_needs_no_login(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/profiles").status_code == 401
    assert client.get("/docs").status_code == 404


def test_registration_requires_valid_key(client, admin):
    assert register(client, "NM-AAAAA-AAAAA-AAAAA-AAAAA").json()["detail"]["code"] == "invalid_license_key"
    key = new_key(client, admin)
    r = register(client, key.lower())                      # klíč nezávisí na velikosti písmen
    assert r.status_code == 201 and r.json()["user"]["email"] == "user@example.org"
    assert r.json()["user"]["is_admin"] is False
    # jednorázový klíč podruhé nejde použít
    assert register(client, key, "druhy@example.org").status_code == 403
    assert login(client, "user@example.org").status_code == 200


def test_key_is_not_consumed_by_failed_registration(client, admin):
    key = new_key(client, admin)
    assert register(client, key, "neni-email").status_code == 422
    assert register(client, key, password="kratke").json()["detail"]["code"] == "weak_password"
    assert register(client, key, "admin@example.org").status_code == 409
    assert register(client, key).status_code == 201


def test_multi_use_expired_and_revoked_keys(app, client, admin):
    multi = new_key(client, admin, max_uses=2)
    assert register(client, multi, "a@example.org").status_code == 201
    assert register(client, multi, "b@example.org").status_code == 201
    assert register(client, multi, "c@example.org").status_code == 403

    expired = new_key(client, admin, valid_days=1)
    with app.state.session_factory() as db:
        obj = db.scalar(select(LicenseKey).where(LicenseKey.key_hash == security.sha256(expired)))
        obj.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert register(client, expired, "d@example.org").status_code == 403

    revoked = new_key(client, admin)
    key_id = [k for k in client.get("/admin/keys", headers=admin).json() if k["state"] == "active"][0]["id"]
    assert client.post(f"/admin/keys/{key_id}/revoke", headers=admin).json()["state"] == "revoked"
    assert register(client, revoked, "e@example.org").status_code == 403


def test_key_stored_only_as_hash_and_listed_with_note(app, client, admin):
    r = client.post("/admin/keys", json={"note": "Pavel – kancelář"}, headers=admin)
    key = r.json()["key"]
    assert key.startswith("NM-") and len(key) == 26
    register(client, key, "pavel@example.org")
    listed = client.get("/admin/keys", headers=admin).json()[0]
    assert "key" not in listed and listed["note"] == "Pavel – kancelář"
    assert listed["users"] == ["pavel@example.org"] and listed["state"] == "used"
    with app.state.session_factory() as db:
        assert key not in str(db.scalars(select(LicenseKey)).all()[0].__dict__)
    users = {u["email"]: u for u in client.get("/admin/users", headers=admin).json()}
    assert users["pavel@example.org"]["key_note"] == "Pavel – kancelář"


def test_registration_can_be_disabled():
    app = create_app(make_settings(registration_enabled=False))
    r = register(TestClient(app), "NM-AAAAA-AAAAA-AAAAA-AAAAA")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "registration_disabled"


def test_login_errors_do_not_reveal_accounts(app, client):
    add_user(app, "user@example.org")
    wrong = login(client, "user@example.org", password="spatne-heslo-123")
    missing = login(client, "nikdo@example.org")
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


def test_failed_logins_are_limited_per_ip_and_email_without_revealing_accounts(app, client):
    add_user(app, "user@example.org")
    real = [login(client, "user@example.org", password="spatne-heslo-123") for _ in range(6)]
    fake = [login(client, "nikdo@example.org", password="spatne-heslo-123") for _ in range(6)]
    assert [r.status_code for r in real] == [401] * 5 + [429]
    assert [r.status_code for r in fake] == [401] * 5 + [429]           # stejné chování, účet nejde poznat
    assert real[-1].json() == fake[-1].json()
    # ani správné heslo z této IP teď neprojde, ale z jiné IP ano (útočník nemůže účet zamknout vlastníkovi)
    assert login(client, "user@example.org").status_code == 429
    other = TestClient(app, client=("203.0.113.9", 50000))
    assert login(other, "user@example.org").status_code == 200


def test_successful_login_resets_failure_counter(app, client):
    add_user(app, "user@example.org")
    for _ in range(4):
        login(client, "user@example.org", password="spatne-heslo-123")
    assert login(client, "user@example.org").status_code == 200
    assert all(login(client, "user@example.org", password="spatne-heslo-123").status_code == 401 for _ in range(4))


def test_ip_rate_limits():
    app = create_app(make_settings(rate_login=(3, 300), rate_register=(2, 3600)))
    c = TestClient(app)
    codes = [login(c, "x@example.org").status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    codes = [register(c, "NM-AAAAA-AAAAA-AAAAA-AAAAA").status_code for _ in range(3)]
    assert codes == [403, 403, 429]
    assert "Retry-After" in register(c, "x").headers


def test_general_rate_limit_and_body_limit():
    c = TestClient(create_app(make_settings(rate_general=(5, 60))))
    assert [c.get("/profiles").status_code for _ in range(6)][-1] == 429
    assert c.get("/health").status_code == 200
    c = TestClient(create_app(make_settings()))
    assert c.post("/auth/login", content=b"x" * (800 * 1024),
                  headers={"Content-Type": "application/json"}).status_code == 413


def test_refresh_rotation_and_reuse_detection(app, client):
    add_user(app, "user@example.org")
    first = login(client, "user@example.org").json()
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]
    assert client.get("/auth/me", headers=bearer(second)).status_code == 200
    third = client.post("/auth/refresh", json={"refresh_token": second.json()["refresh_token"]})
    assert third.status_code == 200
    # token o dvě výměny starší = zkopírovaný → celá relace končí
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": third.json()["refresh_token"]}).status_code == 401
    assert client.get("/auth/me", headers=bearer(third)).status_code == 401


def test_lost_refresh_response_is_tolerated_briefly(app, client):
    from app.models import UserSession
    add_user(app, "user@example.org")
    first = login(client, "user@example.org").json()["refresh_token"]
    lost = client.post("/auth/refresh", json={"refresh_token": first})        # odpověď se „ztratila“
    retry = client.post("/auth/refresh", json={"refresh_token": first})
    assert lost.status_code == retry.status_code == 200
    again = client.post("/auth/refresh", json={"refresh_token": retry.json()["refresh_token"]})
    assert again.status_code == 200
    # pokus se „ztraceným“ tokenem (nikdy nepoužitým) = podezřelé → relace končí
    assert client.post("/auth/refresh", json={"refresh_token": lost.json()["refresh_token"]}).status_code == 401
    # „ztracený“ token už neplatí, ale klient s novým pokračuje
    assert client.post("/auth/refresh", json={"refresh_token": retry.json()["refresh_token"]}).status_code == 401
    # po uplynutí tolerance už předchozí token neprojde a relaci ukončí
    second = login(client, "user@example.org").json()["refresh_token"]
    ok = client.post("/auth/refresh", json={"refresh_token": second}).json()["refresh_token"]
    with app.state.session_factory() as db:
        s = db.scalar(select(UserSession).where(UserSession.token_hash == security.sha256(ok)))
        s.rotated_at = now() - timedelta(minutes=10)
        db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": second}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": ok}).status_code == 401


def test_legacy_refresh_token_from_1_0_0_still_works(app, client):
    from app.models import UserSession
    add_user(app, "user@example.org")
    login(client, "user@example.org")
    legacy = "stary-token-bez-tecky-" + "x" * 40
    with app.state.session_factory() as db:
        db.scalar(select(UserSession)).token_hash = security.sha256(legacy)
        db.commit()
    r = client.post("/auth/refresh", json={"refresh_token": legacy})
    assert r.status_code == 200 and "." in r.json()["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}).status_code == 200


def test_concurrent_refresh_with_same_token_rotates_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    app = create_app(make_settings(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}", refresh_grace_s=0))
    c = TestClient(app)
    add_user(app, "user@example.org")
    token = login(c, "user@example.org").json()["refresh_token"]
    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(lambda _: c.post("/auth/refresh", json={"refresh_token": token}).status_code, range(6)))
    assert codes.count(200) == 1


def test_logout_revokes_session(app, client):
    add_user(app, "user@example.org")
    r = login(client, "user@example.org")
    assert client.post("/auth/logout", json={}, headers=bearer(r)).status_code == 204
    assert client.get("/auth/me", headers=bearer(r)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}).status_code == 401


def test_password_change_logs_out_other_devices(app, client):
    add_user(app, "user@example.org")
    a, b = login(client, "user@example.org"), login(client, "user@example.org")
    body = {"current_password": PASSWORD, "new_password": "uplne-nove-heslo-1"}
    assert client.post("/auth/password", json={**body, "current_password": "spatne"}, headers=bearer(a)).status_code == 401
    assert client.post("/auth/password", json=body, headers=bearer(a)).status_code == 204
    assert client.get("/auth/me", headers=bearer(a)).status_code == 200
    assert client.get("/auth/me", headers=bearer(b)).status_code == 401
    assert login(client, "user@example.org").status_code == 401
    assert login(client, "user@example.org", password="uplne-nove-heslo-1").status_code == 200


def test_admin_cannot_enable_totp_over_web(app, client):
    """Kdo zná heslo správce, nesmí si sám zapnout 2FA a získat tím správu (zapíná se na serveru)."""
    add_user(app, admin=True)
    r = login(client)
    denied = client.post("/admin/keys", json={"note": "x"}, headers=bearer(r))
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "totp_setup_required"
    assert "totp-setup" in denied.json()["detail"]["message"]
    for path, body in (("/auth/totp/setup", {}), ("/auth/totp/enable", {"code": "123456"})):
        res = client.post(path, json=body, headers=bearer(r))
        assert res.status_code == 403 and res.json()["detail"]["code"] == "totp_cli_required"


def test_enabling_totp_replaces_all_sessions(app, client):
    add_user(app, "user@example.org")
    old = login(client, "user@example.org")
    stolen = old.json()["refresh_token"]                      # zkopírováno před zapnutím 2FA
    setup = client.post("/auth/totp/setup", json={}, headers=bearer(old)).json()
    assert client.post("/auth/totp/enable", json={"code": "000000", "password": PASSWORD},
                       headers=bearer(old)).status_code == 401
    enabled = client.post("/auth/totp/enable", json={"code": code(setup["secret"]), "password": PASSWORD},
                          headers=bearer(old))
    body = enabled.json()
    assert enabled.status_code == 200 and body["mfa"] is True and body["refresh_token"] != stolen
    assert client.post("/auth/refresh", json={"refresh_token": stolen}).status_code == 401
    assert client.get("/auth/me", headers=bearer(old)).status_code == 401
    assert client.get("/auth/me", headers=bearer(enabled)).status_code == 200
    assert login(client, "user@example.org").json()["detail"]["code"] == "totp_required"


def test_totp_code_cannot_be_reused(app, client):
    secret = add_user(app, admin=True, totp=True)
    first = login(client, secret=secret)
    assert first.status_code == 200
    assert login(client, secret=secret).json()["detail"]["code"] == "invalid_totp"


def test_admin_rights_need_recent_totp_not_just_a_stored_token(app, client):
    from app.models import UserSession
    secret = add_user(app, admin=True, totp=True)
    r = login(client, secret=secret)
    assert client.get("/admin/keys", headers=bearer(r)).status_code == 200
    with app.state.session_factory() as db:
        for s in db.scalars(select(UserSession)).all():
            s.mfa_at = now() - timedelta(hours=13)
        db.commit()
    renewed = client.post("/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    denied = client.get("/admin/keys", headers=bearer(renewed))
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "totp_required"
    assert client.get("/profiles", headers=bearer(renewed)).status_code == 200     # běžné funkce dál fungují


def test_totp_secret_is_encrypted_and_legacy_plaintext_is_migrated(tmp_path):
    from app.main import create_app as make
    url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    app1 = make(make_settings(database_url=url))
    secret = add_user(app1, admin=True, totp=True, sealed=False)          # stav z verze 1.0.0
    app2 = make(make_settings(database_url=url))                          # restart = migrace
    with app2.state.session_factory() as db:
        stored = db.scalar(select(User)).totp_secret
    assert stored.startswith("enc1:") and secret not in stored
    assert login(TestClient(app2), secret=secret).status_code == 200


def test_schema_upgrade_adds_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE user_sessions (id CHAR(32) PRIMARY KEY, user_id CHAR(32), token_hash VARCHAR(64), "
                "previous_hash VARCHAR(64), device VARCHAR(100), mfa BOOLEAN, created_at DATETIME, "
                "last_used_at DATETIME, expires_at DATETIME, revoked_at DATETIME)")
    con.commit()
    con.close()
    create_app(make_settings(database_url=f"sqlite:///{path}"))
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(user_sessions)")}
    assert {"mfa_at", "rotated_at"} <= cols


def test_regular_user_cannot_use_admin_api(client, admin):
    key = new_key(client, admin)
    user = bearer(register(client, key))
    for method, path in (("get", "/admin/keys"), ("get", "/admin/users")):
        assert getattr(client, method)(path, headers=user).status_code == 403
    assert client.post("/admin/keys", json={"note": "x"}, headers=user).status_code == 403


def test_admin_can_disable_and_enable_user(client, admin):
    user = register(client, new_key(client, admin))
    uid = user.json()["user"]["id"]
    assert client.post(f"/admin/users/{uid}/disable", headers=admin).json()["is_active"] is False
    assert client.get("/profiles", headers=bearer(user)).status_code == 401
    assert login(client, "user@example.org").status_code == 401
    assert client.post(f"/admin/users/{uid}/enable", headers=admin).status_code == 200
    assert login(client, "user@example.org").status_code == 200
    me = client.get("/auth/me", headers=admin).json()["id"]
    assert client.post(f"/admin/users/{me}/disable", headers=admin).status_code == 409


def test_totp_matches_rfc6238_vector():
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert security.totp_code(secret, 59 // 30) == "287082"
    assert security.totp_verify(secret, "287082", at=59) == 1
    assert security.totp_verify(secret, "287082", last_step=1, at=59) is None


def test_enabling_totp_needs_the_current_password(app, client):
    """Samotný (třeba ukradený) token k zapnutí 2FA a převzetí účtu nestačí."""
    add_user(app, "user@example.org")
    r = login(client, "user@example.org")
    setup = client.post("/auth/totp/setup", json={}, headers=bearer(r)).json()
    for body in ({"code": code(setup["secret"])},
                 {"code": code(setup["secret"]), "password": "spatne-heslo-123"}):
        res = client.post("/auth/totp/enable", json=body, headers=bearer(r))
        assert res.status_code == 401 and res.json()["detail"]["code"] == "invalid_credentials"
    assert client.get("/auth/me", headers=bearer(r)).json()["totp_enabled"] is False
    assert login(client, "user@example.org").status_code == 200        # účet dál bez 2FA


def test_lost_response_tolerance_is_single_use(app, client):
    add_user(app, "user@example.org")
    first = login(client, "user@example.org").json()["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": first}).status_code == 200   # „ztracená“
    retry = client.post("/auth/refresh", json={"refresh_token": first})
    assert retry.status_code == 200
    # druhé opakování téhož tokenu v okně = přehrání ukradeného tokenu → relace končí
    assert client.post("/auth/refresh", json={"refresh_token": first}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": retry.json()["refresh_token"]}).status_code == 401


def test_forged_token_with_known_session_id_does_not_end_the_session(app, client):
    add_user(app, "user@example.org")
    real = login(client, "user@example.org").json()["refresh_token"]
    sid = real.split(".")[0]
    other = security.new_refresh_token(__import__("uuid").uuid4(), "y" * 48).split(".", 1)[1]
    for forged in (f"{sid}.cokoliv", f"{sid}.{other}", f"{sid}.abc.def"):
        assert client.post("/auth/refresh", json={"refresh_token": forged}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": real}).status_code == 200


def test_unsigned_token_from_1_2_0_still_rotates(app, client):
    from app.models import UserSession
    add_user(app, "user@example.org")
    login(client, "user@example.org")
    with app.state.session_factory() as db:
        s = db.scalar(select(UserSession))
        legacy = f"{s.id.hex}.{'a' * 64}"                          # formát 1.1.0–1.2.0 bez podpisu
        s.token_hash = security.sha256(legacy)
        db.commit()
    r = client.post("/auth/refresh", json={"refresh_token": legacy})
    assert r.status_code == 200 and security.refresh_token_issued("x" * 48, r.json()["refresh_token"])
    assert client.post("/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}).status_code == 200


def test_changed_jwt_secret_gives_clear_error_and_totp_key_keeps_2fa(tmp_path):
    url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    first = create_app(make_settings(database_url=url))
    secret = add_user(first, "user@example.org", totp=True)
    changed = TestClient(create_app(make_settings(database_url=url, jwt_secret="z" * 48)))
    r = login(changed, "user@example.org", secret=secret)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "totp_unavailable"
    kept = TestClient(create_app(make_settings(database_url=url, jwt_secret="w" * 48, totp_key="x" * 48)))
    assert login(kept, "user@example.org", secret=secret).status_code == 200
