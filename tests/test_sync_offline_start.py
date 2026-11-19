"""Obnova uložené relace po startu bez sítě, bez skutečných síťových požadavků."""
import json
import time
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from netmonitor.core.config import AppConfig, PingTarget
from netmonitor.core.profiles import ProfileStore
from netmonitor.gui.controller import Controller
from netmonitor.gui.sync_service import SyncService

from .fakes import FakePinger
from .test_gui_smoke import qapp  # noqa: F401
from .test_sync_client import Response

URL = "https://example.com"
EMAIL = "user@example.com"


class Server:
    """Transport v paměti: offline OSError, obnova tokenu, revokace 401 a nahrání profilu."""
    def __init__(self):
        self.offline = False
        self.revoked = False
        self.invalid_response = False
        self.attempts = 0
        self.requests = []
        self.uploaded = None

    def open(self, req, timeout=None):
        assert urlsplit(req.full_url).netloc == "example.com"
        self.attempts += 1
        if self.offline:
            raise OSError("síť není dostupná")
        method, path = req.get_method(), urlsplit(req.full_url).path
        self.requests.append((method, path))
        if path == "/auth/refresh":
            if self.revoked:
                raise HTTPError(req.full_url, 401, "revoked", Message(), None)
            if self.invalid_response:
                return Response({})
            assert json.loads(req.data)["refresh_token"] == "saved-refresh"
            return Response({"access_token": "test-access", "refresh_token": "rotated-refresh",
                             "user": {"email": EMAIL}})
        assert req.get_header("Authorization") == "Bearer test-access"
        if (method, path) == ("GET", "/profiles"):
            return Response([])
        if (method, path) == ("POST", "/profiles"):
            self.uploaded = json.loads(req.data)
            return Response({**self.uploaded, "id": "11111111-1111-4111-8111-111111111111", "revision": 1})
        raise AssertionError((method, path))


@pytest.fixture
def server(monkeypatch):
    srv = Server()
    monkeypatch.setattr("netmonitor.core.sync_client._open", srv.open)
    return srv


def wait_idle(service):
    """Počká na skutečné doručení výsledku z pracovního vlákna do GUI."""
    deadline = time.monotonic() + 5
    while service.busy and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert not service.busy, service.status


@pytest.fixture(params=["inline", "controller"])
def service(request, qapp, tmp_path, monkeypatch):
    """Obě cesty: přímé dokončení i skutečné vlákno a Controller._on_deliver."""
    monkeypatch.setenv("NETMONITOR_SYNC_URL", URL)
    monkeypatch.setattr("netmonitor.gui.controller.select_pinger", lambda: FakePinger({}))
    cfg = AppConfig.from_dict({"network": {"external_api": False}})
    cfg.ping.targets = [PingTarget("Test", "192.0.2.1", id="test")]
    ctrl = Controller(cfg, tmp_path / "config.json", tmp_path / "history.sqlite3")
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(cfg)
    monkeypatch.setattr(ctrl, "profile_store", store)
    if request.param == "inline":
        monkeypatch.setattr(ctrl, "_run_bg", lambda key, fn, done=None: done(fn()) if done else fn())
    svc = SyncService(ctrl)
    monkeypatch.setattr(ctrl, "sync", svc)
    yield svc
    svc._periodic.stop()
    svc._debounce.stop()
    ctrl.shutdown()
    svc.deleteLater()
    ctrl.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def save_session(service):
    service.secrets.save(URL, EMAIL, "saved-refresh")


@pytest.mark.parametrize("trigger", ["periodic", "manual"])
def test_offline_start_recovers_and_syncs(service, server, trigger):
    save_session(service)
    server.offline = True
    service.start()
    wait_idle(service)
    assert not service.logged_in and "offline" in service.status
    assert service.client.refresh_token and service.secrets.load() is not None
    assert service._periodic.isActive() and service._periodic.interval() == 5 * 60 * 1000
    assert server.requests == []
    assert server.attempts == 1

    # Ani další neúspěch nesmí zablokovat obnovu; žádný profil se offline neodešle.
    service._periodic.timeout.emit()
    wait_idle(service)
    assert not service.logged_in and service._periodic.isActive()
    assert server.requests == []
    assert server.attempts == 2

    server.offline = False
    if trigger == "periodic":
        service._periodic.timeout.emit()
    else:
        service.sync_now()
    wait_idle(service)
    assert service.logged_in and service.client.email == EMAIL
    assert service.last_sync is not None
    assert server.requests[0] == ("POST", "/auth/refresh")
    assert ("GET", "/profiles") in server.requests
    assert server.uploaded["name"] == "Soukromý"
    assert server.uploaded["config"]["ping"]["targets"][0]["host"] == "192.0.2.1"
    assert service.secrets.load() == (URL, EMAIL, "rotated-refresh")


@pytest.mark.parametrize("offline_first", [False, True])
def test_revoked_session_stops_retrying(service, server, offline_first):
    save_session(service)
    server.revoked = True
    if offline_first:
        server.offline = True
        service.start()
        wait_idle(service)
        assert "offline" in service.status
        server.offline = False
        service._periodic.timeout.emit()
    else:
        service.start()
    wait_idle(service)
    assert server.requests == [("POST", "/auth/refresh")]
    assert not service.logged_in and not service.client.refresh_token
    assert service.secrets.load() is None
    assert not service._periodic.isActive() and "Přihlášení vypršelo" in service.status
    server.requests.clear()
    service.sync_now()
    service._periodic.timeout.emit()
    assert server.requests == [] and not service.busy


@pytest.mark.parametrize("stored_server", [None, "https://other.example.com"])
def test_no_matching_session_sends_nothing(service, server, stored_server):
    if stored_server is not None:
        service.secrets.save(stored_server, EMAIL, "test-token")
    service.start()
    service.sync_now()
    service._periodic.timeout.emit()
    assert not service.logged_in and not service.busy
    assert not service._periodic.isActive() and server.requests == []


def test_logout_cancels_offline_retry(service, server):
    save_session(service)
    server.offline = True
    service.start()
    wait_idle(service)
    service.logout()
    server.offline = False
    service.sync_now()
    service._periodic.timeout.emit()
    assert server.requests == [] and not service.logged_in
    assert not service._periodic.isActive() and service.secrets.load() is None


def test_invalid_response_does_not_schedule_retry(service, server):
    save_session(service)
    server.invalid_response = True
    service.start()
    wait_idle(service)
    assert not service.logged_in and not service._periodic.isActive()
    assert "Přihlášení nelze obnovit" in service.status
    assert "offline" not in service.status
    server.requests.clear()
    service.sync_now()
    service._periodic.timeout.emit()
    assert server.requests == [] and not service.busy
