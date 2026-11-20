"""Přehled zařízení účtu pro správce: poslední IP, aktivita, odhlášení zařízení, mazání starých záznamů."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import UserSession, now

from .conftest import PASSWORD, bearer, new_key, register


def at(app, ip):
    return TestClient(app, client=(ip, 50000))


def test_admin_sees_devices_with_last_ip_and_can_log_one_out(app, client, admin):
    key = new_key(client, admin, max_uses=1)
    laptop = register(at(app, "203.0.113.10"), key)
    phone_client = at(app, "198.51.100.7")
    phone = phone_client.post("/auth/login", json={"email": "user@example.org", "password": PASSWORD,
                                                  "device": "Telefon"})
    assert phone_client.get("/profiles", headers=bearer(phone)).status_code == 200
    row = next(u for u in client.get("/admin/users", headers=admin).json() if u["email"] == "user@example.org")
    assert row["last_ip"] == "198.51.100.7" and row["devices"] == 2 and row["last_seen_at"]

    devices = client.get(f"/admin/users/{row['id']}/sessions", headers=admin).json()
    assert {d["ip"] for d in devices} == {"203.0.113.10", "198.51.100.7"}
    assert all(d["active"] for d in devices) and {d["device"] for d in devices} >= {"Telefon"}

    target = next(d for d in devices if d["ip"] == "198.51.100.7")
    assert client.post(f"/admin/sessions/{target['id']}/revoke", headers=admin).json()["active"] is False
    assert phone_client.get("/profiles", headers=bearer(phone)).status_code == 401
    assert at(app, "203.0.113.10").get("/profiles", headers=bearer(laptop)).status_code == 200
    row = next(u for u in client.get("/admin/users", headers=admin).json() if u["email"] == "user@example.org")
    assert row["devices"] == 1


def test_ip_follows_device_after_network_change(app, client, admin):
    r = register(at(app, "203.0.113.10"), new_key(client, admin))
    moved = at(app, "192.0.2.55").post("/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    assert moved.status_code == 200
    uid = r.json()["user"]["id"]
    devices = client.get(f"/admin/users/{uid}/sessions", headers=admin).json()
    assert [d["ip"] for d in devices] == ["192.0.2.55"]


def test_admin_cannot_revoke_own_current_session_and_regular_user_has_no_access(app, client, admin):
    me = client.get("/auth/me", headers=admin).json()["id"]
    own = client.get(f"/admin/users/{me}/sessions", headers=admin).json()[0]
    assert client.post(f"/admin/sessions/{own['id']}/revoke", headers=admin).status_code == 409
    user = bearer(register(client, new_key(client, admin)))
    assert client.get(f"/admin/users/{me}/sessions", headers=user).status_code == 403
    assert client.post(f"/admin/sessions/{own['id']}/revoke", headers=user).status_code == 403


def test_old_ended_sessions_are_deleted_after_30_days(app, client, admin):
    r = register(client, new_key(client, admin))
    client.post("/auth/logout", json={}, headers=bearer(r))
    with app.state.session_factory() as db:
        for s in db.scalars(select(UserSession).where(UserSession.revoked_at.is_not(None))).all():
            s.revoked_at = now() - timedelta(days=31)
        db.commit()
    client.post("/auth/login", json={"email": "user@example.org", "password": PASSWORD})   # úklid při přihlášení
    with app.state.session_factory() as db:
        assert db.scalars(select(UserSession).where(UserSession.revoked_at < now() - timedelta(days=30))).all() == []
