import time
from sqlalchemy import select

from app import cli, security
from app.models import LicenseKey, User

from .conftest import PASSWORD, add_user, login, register


def answers(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *_: next(it))


def test_create_admin_with_totp_in_terminal(app, client, monkeypatch, capsys):
    factory, key = app.state.session_factory, app.state.settings.jwt_secret
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: PASSWORD)
    secrets = []
    real_new = security.new_totp_secret
    monkeypatch.setattr(security, "new_totp_secret", lambda: secrets.append(real_new()) or secrets[-1])
    monkeypatch.setattr(security, "totp_qr_terminal", lambda uri: print("QR", uri))
    codes = iter(["000000"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "Spravce@Example.org" if "E-mail" in prompt
                        else next(codes, None) or security.totp_code(secrets[-1], int(time.time() // 30)))
    assert cli.main(["create-admin"], factory, key) == 0
    out = capsys.readouterr().out
    assert "QR otpauth://" in out and "zapnuté" in out
    with factory() as db:
        user = db.scalar(select(User))
        assert user.email == "spravce@example.org" and user.is_admin and user.totp_enabled
        assert user.totp_secret.startswith("enc1:")
    login_ok = client.post("/auth/login", json={"email": "spravce@example.org", "password": PASSWORD,
                                                "totp_code": security.totp_code(secrets[-1], int(time.time() // 30) + 1)})
    assert login_ok.status_code == 200
    r = client.get("/admin/keys", headers={"Authorization": "Bearer " + login_ok.json()["access_token"]})
    assert r.status_code == 200


def test_promoting_existing_user_resets_self_enrolled_totp(app, monkeypatch):
    factory, key = app.state.session_factory, app.state.settings.jwt_secret
    add_user(app, "user@example.org", totp=True)
    answers(monkeypatch, "user@example.org", "a", "x", "x", "x")             # 3× špatný kód
    assert cli.main(["create-admin"], factory, key) == 0
    with factory() as db:
        user = db.scalar(select(User))
        assert user.is_admin and not user.totp_enabled and user.totp_secret is None


def test_key_and_resets(app, client, monkeypatch, capsys):
    factory, key_secret = app.state.session_factory, app.state.settings.jwt_secret
    add_user(app, "spravce@example.org", admin=True)
    capsys.readouterr()
    assert cli.main(["create-key", "Jana – účetní", "--days", "7"], factory, key_secret) == 0
    key = capsys.readouterr().out.strip()
    with factory() as db:
        obj = db.scalar(select(LicenseKey))
        assert obj.note == "Jana – účetní" and obj.key_hash == security.sha256(key) and obj.expires_at
    assert register(client, key).status_code == 201

    secret = add_user(app, "druhy@example.org", admin=True, totp=True)
    session = login(client, "druhy@example.org", secret=secret)
    answers(monkeypatch, "n")
    assert cli.main(["reset-totp", "druhy@example.org"], factory, key_secret) == 0
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + session.json()["access_token"]}).status_code == 401
    assert login(client, "druhy@example.org").status_code == 200       # bez kódu, 2FA je vypnuté

    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "jine-dlouhe-heslo")
    assert cli.main(["reset-password", "druhy@example.org"], factory, key_secret) == 0
    assert login(client, "druhy@example.org").status_code == 401
    assert login(client, "druhy@example.org", password="jine-dlouhe-heslo").status_code == 200
