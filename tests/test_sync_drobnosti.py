"""Regrese L47: synchronizace, obnova tokenu a odhlášení bez skutečné sítě."""
import json
import logging
import os
import threading
import time
from email.message import Message
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from netmonitor.core import sync_engine
from netmonitor.core.sync_client import SyncClient, SyncError
from netmonitor.gui.sync_service import SyncService, _safe

from .test_sync_client import Response
from .test_sync_service import service, wait_for  # noqa: F401
from .test_gui_smoke import pump, qapp  # noqa: F401

URL = "https://example.com"
EMAIL = "user@example.com"
SECRET = "testovaci-tajny-obsah"
PID = "11111111-1111-4111-8111-111111111111"


def test_safe_debug_trace_has_frames_without_source_or_exception(caplog):
    def fail():
        raise ValueError("testovaci-tajny-obsah")

    caplog.set_level(logging.DEBUG, logger="netmonitor.gui.sync_service")
    result = _safe(fail)()
    assert isinstance(result.value, SyncError)
    assert "ValueError" in caplog.text
    assert SECRET not in caplog.text
    assert "raise ValueError" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    debug = "".join(record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG)
    assert f"{os.path.basename(__file__)}:{fail.__code__.co_firstlineno + 1} in fail" in debug
    assert os.path.dirname(__file__) not in debug


def authorize(service):
    service.client.access_token = "test-access"
    service.client.refresh_token = "test-refresh"
    service.client.user = {"email": EMAIL}
    service._periodic.start()


@pytest.mark.parametrize("failure", ["network", "apply", "unauthorized", "logout"])
def test_pending_sync_cleared_on_every_failure(service, monkeypatch, failure):
    authorize(service)
    completions = []
    monkeypatch.setattr(service.ctrl, "_run_bg", lambda key, fn, done=None: completions.append(done))
    service.sync_now()
    service.sync_now()
    assert service.busy and service._again
    if failure == "apply":
        def fail_apply(*args):
            raise OSError("Zápis není dostupný")
        monkeypatch.setattr(sync_engine, "apply", fail_apply)
        result = []
    elif failure in ("unauthorized", "logout"):
        service.client.access_token = service.client.refresh_token = service.client.user = None
        result = SyncError("Relace skončila", 401, "unauthorized") if failure == "unauthorized" else []
    else:
        result = SyncError("Síť není dostupná", code="network_error")
    completions.pop()(SimpleNamespace(value=result))
    assert not service.busy
    assert not service._again
    pump(0.03)
    assert not completions  # Chyba sama nesmí založit rychlou smyčku opakování.


def test_next_periodic_attempt_uploads_latest_change_once(service, monkeypatch):
    authorize(service)
    entered, release = threading.Event(), threading.Event()
    requests, uploads = [], []

    def open_request(req, timeout):
        path = urlsplit(req.full_url).path
        requests.append((req.get_method(), path))
        if path == "/profiles" and req.get_method() == "GET":
            if len(requests) == 1:
                entered.set()
                assert release.wait(3), "Test neuvolnil požadavek"
                raise OSError("Síť není dostupná")
            return Response([])
        assert (req.get_method(), path) == ("POST", "/profiles")
        payload = json.loads(req.data)
        uploads.append(payload)
        return Response({**payload, "id": PID, "revision": 1})

    monkeypatch.setattr("netmonitor.core.sync_client._open", open_request)
    service.sync_now()
    try:
        assert entered.wait(3)
        cfg = service.ctrl.cfg.copy()
        cfg.ping.targets[0].host = "198.51.100.1"
        service.ctrl.update_config(cfg)
        service.sync_now()
        assert service._again
    finally:
        release.set()
    wait_for(lambda: not service.busy)
    assert not service._again
    pump(0.03)
    assert len(requests) == 1
    service._periodic.timeout.emit()
    wait_for(lambda: not service.busy)
    pump(0.03)
    assert len(uploads) == 1 and len(requests) == 3
    assert uploads[0]["config"]["ping"]["targets"][0]["host"] == "198.51.100.1"
    assert service.last_sync is not None


def test_pending_change_after_success_syncs_again(service, monkeypatch):
    authorize(service)
    entered, release = threading.Event(), threading.Event()
    snapshots = []

    def run(client, snap, resolutions):
        snapshots.append(snap)
        if len(snapshots) == 1:
            entered.set()
            assert release.wait(3), "Test neuvolnil synchronizaci"
        return []

    monkeypatch.setattr(sync_engine, "run", run)
    service.sync_now()
    try:
        assert entered.wait(3)
        cfg = service.ctrl.cfg.copy()
        cfg.ping.targets[0].host = "198.51.100.1"
        service.ctrl.update_config(cfg)
        service.sync_now()
    finally:
        release.set()
    wait_for(lambda: len(snapshots) == 2 and not service.busy)
    assert snapshots[0].profiles[0].cfg.ping.targets[0].host == "192.0.2.1"
    assert snapshots[1].profiles[0].cfg.ping.targets[0].host == "198.51.100.1"
    assert not service._again


@pytest.mark.parametrize("action,code", [("password", "invalid_credentials"), ("totp", "invalid_totp"),
                                        ("totp", "totp_required"), ("password", "unknown_error")])
def test_business_401_is_not_retried(monkeypatch, action, code):
    requests = []

    def open_request(req, timeout):
        path = urlsplit(req.full_url).path
        requests.append(path)
        if path == "/auth/refresh":
            return Response({"access_token": "new-access", "refresh_token": "new-refresh"})
        body = json.dumps({"detail": {"code": code, "message": "Ověření se nezdařilo"}}).encode()
        raise HTTPError(req.full_url, 401, "Unauthorized", Message(), BytesIO(body))

    monkeypatch.setattr("netmonitor.core.sync_client._open", open_request)
    client = SyncClient(URL, access_token="test-access", refresh_token="test-refresh")
    with pytest.raises(SyncError) as error:
        if action == "password":
            client.change_password("chybne-testovaci-heslo", "nove-testovaci-heslo")
        else:
            client.totp_enable("000000")
    assert error.value.code == code
    assert requests == ["/auth/password" if action == "password" else "/auth/totp/enable"]


@pytest.mark.parametrize("second_401", [False, True])
@pytest.mark.parametrize("body", [b'{"detail": {"code": "unauthorized"}}',
                                  b'{"detail": {"code": ""}}', b'{"detail": {}}',
                                  b"", b"<html>Unauthorized</html>"])
def test_access_401_refreshes_and_retries_at_most_once(monkeypatch, second_401, body):
    requests = []

    def open_request(req, timeout):
        path = urlsplit(req.full_url).path
        requests.append(path)
        if path == "/auth/refresh":
            return Response({"access_token": "new-access", "refresh_token": "new-refresh"})
        if len(requests) == 1 or second_401:
            raise HTTPError(req.full_url, 401, "Unauthorized", Message(), BytesIO(body))
        assert req.get_header("Authorization") == "Bearer new-access"
        return Response([])

    monkeypatch.setattr("netmonitor.core.sync_client._open", open_request)
    client = SyncClient(URL, access_token="test-access", refresh_token="test-refresh")
    if second_401:
        with pytest.raises(SyncError):
            client.profiles()
    else:
        assert client.profiles() == []
    assert requests == ["/profiles", "/auth/refresh", "/profiles"]


@pytest.mark.parametrize("during", ["refresh", "callback"])
def test_logout_during_refresh_keeps_session_deleted(service, monkeypatch, during):
    authorize(service)
    service.secrets.save(URL, EMAIL, "test-refresh")
    entered, release = threading.Event(), threading.Event()
    client = service.client
    callback = client.on_tokens

    def blocked_callback(refresh):
        if during == "callback":
            entered.set()
            assert release.wait(3), "Test neuvolnil callback"
        callback(refresh)

    client.on_tokens = blocked_callback

    def open_request(req, timeout):
        path = urlsplit(req.full_url).path
        if path == "/auth/refresh":
            if during == "refresh":
                entered.set()
                assert release.wait(3), "Test neuvolnil obnovu"
            return Response({"access_token": "new-access", "refresh_token": "new-refresh",
                             "user": {"email": EMAIL}})
        assert path in ("/profiles", "/auth/logout")
        return Response([] if path == "/profiles" else {})

    monkeypatch.setattr("netmonitor.core.sync_client._open", open_request)
    client.access_token = None  # Obnova uvnitř _request drží zámek klienta ve workeru.
    service.sync_now()
    try:
        assert entered.wait(3)
        started = time.monotonic()
        service.logout()
        assert time.monotonic() - started < 1
        assert not service.secrets.path.exists()
    finally:
        release.set()
    wait_for(lambda: not service.busy)
    assert not service.secrets.path.exists()
    assert not service.logged_in and not service.client.refresh_token


def test_stale_callback_does_not_overwrite_new_session(service, monkeypatch):
    authorize(service)
    old_callback = service.client.on_tokens
    monkeypatch.setattr(service.ctrl, "_run_bg", lambda *args: None)
    service.logout()
    authorize(service)
    service.client.on_tokens("new-session-refresh")
    old_callback("old-session-refresh")
    saved_is_current = service.secrets.load() == (URL, EMAIL, "new-session-refresh")
    assert saved_is_current
    old_callback(None)
    saved_is_current = service.secrets.load() == (URL, EMAIL, "new-session-refresh")
    assert saved_is_current


@pytest.mark.parametrize("operation", ["sync", "account"])
@pytest.mark.parametrize("access_token", [None, "expired-access"])
@pytest.mark.parametrize("failed", [False, True])
def test_logout_revokes_late_refresh_session(service, monkeypatch, operation, access_token, failed):
    authorize(service)
    client = service.client
    client.access_token = access_token
    entered, release = threading.Event(), threading.Event()
    revoked, results = [], []

    def open_request(req, timeout):
        path = urlsplit(req.full_url).path
        if path == "/auth/refresh":
            entered.set()
            assert release.wait(3), "Test neuvolnil obnovu"
            return Response({"access_token": "new-access", "refresh_token": "new-refresh",
                             "user": {"email": EMAIL}})
        if path == "/auth/logout" and req.get_header("Authorization") == "Bearer new-access":
            revoked.append(True)
            return Response({})
        if req.get_header("Authorization") == "Bearer expired-access":
            body = b'{"detail": {"code": "unauthorized"}}'
            raise HTTPError(req.full_url, 401, "Unauthorized", Message(), BytesIO(body))
        assert path == "/profiles"
        if failed:
            raise OSError("Testovací výpadek po obnově")
        return Response([])

    monkeypatch.setattr("netmonitor.core.sync_client._open", open_request)
    if operation == "sync":
        service.sync_now()
    else:
        service.run("profiles", client.profiles, results.append)
    try:
        assert entered.wait(3)
        service.logout()
        # Pozdní úklid nesmí zrušit ani přepsat novou místní relaci.
        authorize(service)
        service.client.on_tokens("new-session-refresh")
    finally:
        release.set()
    wait_for(lambda: not service.busy if operation == "sync" else bool(results))
    assert client.access_token is client.refresh_token is client.user is None
    assert not client.mfa
    assert service.last_sync is None           # starý výsledek se neaplikuje na novou relaci
    wait_for(lambda: bool(revoked))
    assert revoked == [True]
    saved_is_current = service.secrets.load() == (URL, EMAIL, "new-session-refresh")
    assert saved_is_current


@pytest.mark.parametrize("override", [URL, None, "", "neplatna-url"])
@pytest.mark.parametrize("operation", ["login", "set_server"])
def test_environment_server_overrides_saved_settings(service, monkeypatch, override, operation):
    stored = {"server": "https://saved.example.com", "email": EMAIL}
    service._settings_path.parent.mkdir(parents=True, exist_ok=True)
    service._settings_path.write_text(json.dumps(stored), encoding="utf-8")
    if override is None:
        monkeypatch.delenv("NETMONITOR_SYNC_URL", raising=False)
    else:
        monkeypatch.setenv("NETMONITOR_SYNC_URL", override)
    svc = SyncService(service.ctrl)
    try:
        assert svc.settings == stored
        assert svc.client.base_url == (URL if override == URL else stored["server"])
        if operation == "set_server":
            svc.set_server("https://chosen.example.com")
            if override != URL:
                stored["server"] = "https://chosen.example.com"
        else:
            def login(client, *args):
                client._accept({"access_token": "test-access", "refresh_token": "test-refresh",
                                "user": {"email": EMAIL}})
            monkeypatch.setattr(SyncClient, "login", login)
            monkeypatch.setattr(sync_engine, "run", lambda *args: [])
            results = []
            svc.login(EMAIL, "test-password", None, False, results.append)
            wait_for(lambda: bool(results) and not svc.busy)
            assert results == [None]
        svc._save_settings()
        assert svc.client.base_url == (URL if override == URL else stored["server"])
        assert json.loads(svc._settings_path.read_text(encoding="utf-8")) == stored
        monkeypatch.delenv("NETMONITOR_SYNC_URL", raising=False)
        assert svc._load_settings() == stored
    finally:
        svc._periodic.stop()
        svc._debounce.stop()
        svc.ctrl.config_changed.disconnect(svc._local_change)
        svc.ctrl.profiles_changed.disconnect(svc._local_change)
        svc.deleteLater()


@pytest.mark.xfail(reason="Časový limit ukončení řeší PR #15; L47 do controlleru nezasahuje", strict=True)
def test_shutdown_has_bounded_wait_for_running_sync(service, monkeypatch):
    """Otevřené v PR #15: i po _stopping dobíhá celá série požadavků."""
    authorize(service)
    # První GET a dva zápisy tvoří jeden běžící job v poolu controlleru.
    service.ctrl.profile_store.create("Druhý", service.ctrl.cfg.copy())
    entered = threading.Event()
    finished = threading.Event()
    requests = []
    run = sync_engine.run

    def tracked_run(*args):
        try:
            return run(*args)
        finally:
            finished.set()

    def open_request(req, timeout):
        assert timeout == 12
        requests.append(req.get_method())
        entered.set()
        time.sleep(1.1)
        if req.get_method() == "GET":
            return Response([])
        payload = json.loads(req.data)
        return Response({**payload, "id": PID, "revision": 1})

    monkeypatch.setattr("netmonitor.core.sync_client._open", open_request)
    monkeypatch.setattr(sync_engine, "run", tracked_run)
    service.sync_now()
    assert entered.wait(3)
    started = time.monotonic()
    service.ctrl.shutdown()
    elapsed = time.monotonic() - started
    # Po integraci PR #15 může shutdown vrátit dříve než worker. Test musí
    # dokončit falešný transport ještě před obnovením původního _open.
    assert finished.wait(5), "Synchronizace v testu nedoběhla"
    assert elapsed < 3, f"Ukončení čekalo {elapsed:.1f} s na {len(requests)} požadavky"
