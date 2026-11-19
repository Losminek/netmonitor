"""Chyby synchronizace přes skutečné vlákno a doručení výsledku do GUI (bez sítě)."""
import gc
import http.client
import logging
import time
from typing import Any

import pytest

from netmonitor.core.config import AppConfig, PingTarget
from netmonitor.core.profiles import ProfileStore
from netmonitor.core.sync_client import SyncClient, SyncError
from netmonitor.gui.controller import Controller
from netmonitor.gui.sync_service import SyncService

from .fakes import FakePinger
from .sync_server_fake import PASSWORD, server  # noqa: F401
from .test_gui_smoke import pump, qapp  # noqa: F401

URL = "https://example.com"
EMAIL = "user@example.com"
SECRET = "testovaci-heslo-a-token-nepatri-do-logu"


def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        pump(0.02)
    assert predicate(), "Výsledek úlohy nebyl doručen do GUI."


@pytest.fixture
def service(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("NETMONITOR_SYNC_URL", URL)
    monkeypatch.setattr("netmonitor.gui.controller.select_pinger", lambda: FakePinger({}))
    cfg = AppConfig.from_dict({"network": {"external_api": False}})
    cfg.ping.targets = [PingTarget("Test", "192.0.2.1")]
    ctrl: Any = Controller(cfg, tmp_path / "config.json", tmp_path / "history.sqlite3")
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(cfg)
    ctrl.profile_store = store
    svc = SyncService(ctrl)
    ctrl.sync = svc
    yield svc
    svc._periodic.stop()
    svc._debounce.stop()
    ctrl.shutdown()
    pump(0.02)
    # Vlastní Qt objekty uvolníme dříve, než začne další test zpracovávat události.
    ctrl.sync = None
    ctrl.config_changed.disconnect(svc._local_change)
    ctrl.profiles_changed.disconnect(svc._local_change)
    del svc, ctrl
    gc.collect()


def login(service):
    results = []
    service.login(EMAIL, PASSWORD, None, False, results.append)
    wait_for(lambda: bool(results))
    assert results == [None]
    wait_for(lambda: not service.busy)
    assert service.last_sync is not None


def test_wrong_password_then_login_without_restart(service, server):
    server.add_user(EMAIL)
    results = []
    service.login(EMAIL, "chybne-testovaci-heslo", None, False, results.append)
    wait_for(lambda: bool(results))
    assert len(results) == 1 and isinstance(results[0], SyncError)
    assert results[0].code == "invalid_credentials"
    assert not service.busy and not service.logged_in
    assert "Nepřihlášeno:" in service.status and str(results[0]) in service.status

    login(service)
    assert service.logged_in and "Synchronizováno" in service.status
    assert sorted(server.profiles(EMAIL)) == ["Soukromý"]


def test_invalid_license_then_register_without_restart(service, server):
    results = []
    service.register(EMAIL, PASSWORD, "NM-AAAAA-AAAAA-AAAAA-AAAAA", False, results.append)
    wait_for(lambda: bool(results))
    assert len(results) == 1 and isinstance(results[0], SyncError)
    assert results[0].code == "invalid_license_key"
    assert not service.busy and str(results[0]) in service.status

    secret = server.add_admin("admin@example.com")
    admin = SyncClient(URL)
    admin.login("admin@example.com", PASSWORD, server.code(secret))
    key = admin.admin_create_key("Test")
    results.clear()
    service.register(EMAIL, PASSWORD, key["key"], False, results.append)
    wait_for(lambda: bool(results))
    assert results == [None]
    wait_for(lambda: not service.busy)
    assert service.logged_in and service.last_sync is not None


@pytest.mark.parametrize("error", [SyncError("Server není dostupný"),
                                 http.client.IncompleteRead(SECRET.encode()), ValueError(SECRET)])
def test_sync_error_allows_immediate_retry(service, server, monkeypatch, caplog, error):
    server.add_user(EMAIL)
    login(service)
    last_sync = service.last_sync
    profiles = service.client.profiles

    def fail():
        raise error

    monkeypatch.setattr(service.client, "profiles", fail)
    caplog.set_level(logging.ERROR, logger="netmonitor.gui.sync_service")
    service.sync_now()
    wait_for(lambda: not service.busy)
    assert service.last_sync == last_sync
    assert "Synchronizace odložena:" in service.status
    if isinstance(error, SyncError):
        assert str(error) in service.status
        assert not caplog.records
    else:
        assert "neočekávané chybě" in service.status
        assert type(error).__name__ in caplog.text
        assert SECRET not in caplog.text and SECRET not in service.status
        assert all(record.exc_info is None for record in caplog.records)

    monkeypatch.setattr(service.client, "profiles", profiles)
    service.sync_now()
    wait_for(lambda: not service.busy)
    assert service.last_sync != last_sync
    assert "Synchronizováno" in service.status


@pytest.mark.parametrize("error", [SyncError("Nesprávný kód", 401, "invalid_totp"), ValueError(SECRET)])
def test_account_action_delivers_error_and_next_result(service, caplog, error):
    def fail():
        raise error

    results = []
    caplog.set_level(logging.ERROR, logger="netmonitor.gui.sync_service")
    service.run("test", fail, results.append)
    wait_for(lambda: bool(results))
    assert len(results) == 1 and isinstance(results[0], SyncError)
    if isinstance(error, SyncError):
        assert results[0] is error
        assert results[0].status == 401 and results[0].code == "invalid_totp"
    else:
        assert "neočekávané chybě" in str(results[0])
        assert "ValueError" in caplog.text
        assert SECRET not in caplog.text and SECRET not in str(results[0])

    results.clear()
    service.run("test", lambda: {"ok": True}, results.append)
    wait_for(lambda: bool(results))
    assert results == [{"ok": True}]


@pytest.mark.parametrize("blocked", ["same-key", "stopping"])
def test_job_not_started_does_not_leave_busy(service, blocked):
    """Úloha, kterou Controller odmítne spustit, nesmí nechat busy nastavené (L37)."""
    if blocked == "same-key":
        service.ctrl._busy.add("sync-login")
    else:
        service.ctrl._stopping = True
    results = []
    try:
        service.login(EMAIL, PASSWORD, None, False, results.append)
        assert len(results) == 1 and isinstance(results[0], SyncError)
        assert not service.busy
    finally:
        service.ctrl._busy.discard("sync-login")
        service.ctrl._stopping = False
