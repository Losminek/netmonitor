"""Regrese B01/B03: poslední konfigurace a ukončení při zablokované práci."""
import os
import subprocess
import sys
import textwrap
import threading
import time
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from netmonitor.core import config as cfgmod
from netmonitor.core.config import AppConfig, PingTarget
from netmonitor.core.ping_engine import PingResult, PingStatus
from netmonitor.gui import controller as cmod
from netmonitor.utils.workers import DaemonPool

from .fakes import FakePinger


@pytest.fixture
def ctrl(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(cmod, "select_pinger", FakePinger)
    cfg = AppConfig.from_dict({"network": {"external_api": False}})
    cfg.ping.targets = [PingTarget(name="Test", host="example.com", id="test")]
    path = tmp_path / "config.json"
    cfgmod.save_config(cfg, path)
    controller = cmod.Controller(cfg, path, tmp_path / "history.sqlite3")
    yield controller
    controller.shutdown()
    app.processEvents()


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Podmínka nebyla splněna v limitu"
        QCoreApplication.processEvents()
        time.sleep(0.005)


@pytest.mark.parametrize("update", [False, True])
def test_immediate_shutdown_saves_latest_config(ctrl, update):
    """Regrese B01: debounce nesmí zahodit přesun okna ani změnu nastavení."""
    cfg = ctrl.cfg.copy()
    cfg.appearance.locked = True
    if update:
        ctrl.update_config(cfg)
    else:
        ctrl.cfg = cfg
        ctrl.schedule_save()
    ctrl.shutdown()
    assert cfgmod.load_config(ctrl.config_path).appearance.locked is True


@pytest.mark.parametrize("fail_first", [False, True])
def test_newer_snapshot_survives_slow_save(ctrl, monkeypatch, fail_first):
    """Regrese B01: novější revize se uloží i při obsazeném klíči save-config."""
    entered, release = threading.Event(), threading.Event()
    save = cfgmod.save_config
    calls = []

    def slow_save(cfg, path):
        calls.append(cfg.appearance.locked)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            if fail_first:
                raise OSError("Test chyby prvního zápisu")
        save(cfg, path)

    monkeypatch.setattr(cfgmod, "save_config", slow_save)
    try:
        ctrl.schedule_save()
        ctrl._save_now()
        assert entered.wait(2)
        ctrl.cfg.appearance.locked = True
        ctrl.schedule_save()
        ctrl._save_timer.stop()
        ctrl._save_now()  # debounce vypršel během prvního zápisu
        release.set()
        wait_for(lambda: len(calls) == 2 and "save-config" not in ctrl._busy)
        assert cfgmod.load_config(ctrl.config_path).appearance.locked is True
        assert calls == [False, True]
    finally:
        release.set()


@pytest.mark.parametrize("blocked", ["pinger", "resolver", "network"])
def test_shutdown_deadline_with_blocked_worker(ctrl, monkeypatch, blocked):
    """Regrese B03: visící DNS/ping/nmcli nesmí držet shutdown přes 3 s."""
    entered, release = threading.Event(), threading.Event()

    def hang(*args):
        entered.set()
        release.wait()
        return PingResult(PingStatus.TIMEOUT) if blocked == "pinger" else (None, "Test")

    # Pojistka umožní dokončit i test proti původnímu blokujícímu kódu.
    watchdog = threading.Timer(4, release.set)
    watchdog.start()
    try:
        if blocked == "network":
            ctrl._run_bg("network", hang)
        else:
            if blocked == "resolver":
                monkeypatch.setattr(ctrl.resolver, "resolve", hang)
            else:
                ctrl.cfg.ping.targets[0].host = "192.0.2.1"
                monkeypatch.setattr(ctrl.pinger, "ping", hang)
            ctrl.engine.set_targets(ctrl.cfg.real_targets())
            ctrl.engine.start()
        assert entered.wait(2)
        start = time.monotonic()
        ctrl.shutdown()
        assert time.monotonic() - start < 3
    finally:
        release.set()
        watchdog.cancel()


def test_shutdown_serializes_with_older_save(ctrl, monkeypatch):
    """Regrese B01: rozběhnutý starší zápis nepřepíše poslední shutdown revizi."""
    entered, release = threading.Event(), threading.Event()
    save = cfgmod.save_config

    def slow_save(cfg, path):
        if not cfg.appearance.locked:
            entered.set()
            assert release.wait(5)
        save(cfg, path)

    monkeypatch.setattr(cfgmod, "save_config", slow_save)
    ctrl.schedule_save()
    ctrl._save_now()
    assert entered.wait(2)
    ctrl.cfg.appearance.locked = True
    ctrl.schedule_save()
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        ctrl.shutdown()
        ctrl._bg.shutdown(timeout=1)
        assert cfgmod.load_config(ctrl.config_path).appearance.locked is True
    finally:
        release.set()
        timer.cancel()


def test_shutdown_waits_for_running_ip_sequence(ctrl, monkeypatch, caplog):
    """Regrese B03: již běžící sekvence IP se dokončí před návratem shutdownu."""
    import netmonitor.platform as plat

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def apply(_settings):
        entered.set()
        assert release.wait(5)
        finished.set()
        return True, "Test"

    monkeypatch.setattr(plat, "current", lambda: SimpleNamespace(apply_ip=apply))
    ctrl.apply_ip(object(), lambda _res: pytest.fail("Callback při shutdownu"))
    assert entered.wait(2)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        with caplog.at_level("INFO"):
            ctrl.shutdown()
        assert finished.is_set()
        assert "Čekám na dokončení probíhající změny IP" in caplog.text
        assert "Monitoring ukončen za" in caplog.text
    finally:
        release.set()
        timer.cancel()


def test_shutdown_cancels_queued_work_and_saves(ctrl, monkeypatch):
    """Regrese B01/B03: obsazený pool nezahodí uložení ani nespustí čekající IP."""
    import netmonitor.platform as plat

    release = threading.Event()
    entered = [threading.Event() for _ in range(3)]
    calls = []

    def hang(event):
        event.set()
        release.wait()

    monkeypatch.setattr(plat, "current", lambda: SimpleNamespace(apply_ip=calls.append))
    try:
        for i, event in enumerate(entered):
            ctrl._run_bg(f"blocked-{i}", lambda event=event: hang(event))
        assert all(event.wait(2) for event in entered)
        ctrl.apply_ip(object(), calls.append)
        ctrl.cfg.appearance.locked = True
        ctrl.schedule_save()
        ctrl._save_now()  # zápis teprve čeká ve frontě
        ctrl.shutdown()
        ctrl._run_bg("late", lambda: calls.append("late"))
        assert cfgmod.load_config(ctrl.config_path).appearance.locked is True
    finally:
        release.set()
        ctrl._bg.shutdown(timeout=1)
    assert calls == []


def test_process_exits_with_blocked_dns_and_background(tmp_path):
    """Regrese B03: vrácení shutdown() nestačí, visící workery nesmějí držet Python."""
    script = textwrap.dedent('''
        import sys, threading, time
        from pathlib import Path
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        from netmonitor.core.config import AppConfig, PingTarget
        from netmonitor.gui.controller import Controller
        from tests.fakes import FakePinger

        app = QApplication([])
        cfg = AppConfig.from_dict({"network": {"external_api": False}})
        cfg.ping.targets = [PingTarget(name="Test", host="example.com", id="test")]
        with patch("netmonitor.gui.controller.select_pinger", FakePinger):
            ctrl = Controller(cfg, Path(sys.argv[1]) / "config.json",
                              Path(sys.argv[1]) / "history.sqlite3")
        release = threading.Event()
        dns, bg = threading.Event(), threading.Event()
        def hang(event):
            event.set()
            release.wait()
            return None, "Test"
        ctrl.resolver.resolve = lambda *args: hang(dns)
        ctrl.engine.set_targets(cfg.real_targets())
        ctrl.engine.start()
        ctrl._run_bg("network", lambda: hang(bg))
        assert dns.wait(2) and bg.wait(2)
        start = time.monotonic()
        ctrl.shutdown()
        assert time.monotonic() - start < 3
        print("shutdown dokončen", flush=True)
        # Event se záměrně neuvolní: testujeme konec interpreteru s visícími workery.
    ''')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env,
                            capture_output=True, text=True, timeout=6)
    assert result.returncode == 0, result.stderr
    assert "shutdown dokončen" in result.stdout


def test_monitor_stop_timeout_includes_pool(ctrl, monkeypatch):
    """Regrese B03: timeout stop() zahrnuje workery, nejen scheduler."""
    entered, release = threading.Event(), threading.Event()

    def hang(*args):
        entered.set()
        release.wait()
        return None, "Test"

    monkeypatch.setattr(ctrl.resolver, "resolve", hang)
    ctrl.engine.set_targets(ctrl.cfg.real_targets())
    ctrl.engine.start()
    watchdog = threading.Timer(4, release.set)
    watchdog.start()
    try:
        assert entered.wait(2)
        start = time.monotonic()
        ctrl.engine.stop(timeout=0.05)
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
        watchdog.cancel()


def test_idle_pool_releases_callback_objects():
    """Regrese: hotové callbacky nesmějí držet GUI objekty až do konce workeru."""
    pool = DaemonPool(1, "test-pool")
    value = type("CallbackOwner", (), {})()
    reference = weakref.ref(value)
    try:
        pool.submit(lambda value=value: None).result(timeout=2)
        del value
        wait_for(lambda: reference() is None)
    finally:
        pool.shutdown(timeout=1)


def test_pending_probes_run_after_restart(ctrl, monkeypatch):
    """Regrese B03: restart zachová běžící sondy a neztratí čekající cíle."""
    entered, release = threading.Event(), threading.Event()
    original = ctrl.pinger.ping

    def hang(address, timeout):
        if address == "192.0.2.1":
            entered.set()
            assert release.wait(5)
        return original(address, timeout)

    monkeypatch.setattr(ctrl.pinger, "ping", hang)
    ctrl.engine.max_concurrent = 1
    ctrl.engine.set_targets([PingTarget(name=str(i), host=f"192.0.2.{i}", id=str(i))
                             for i in (1, 2)])
    ctrl.engine.start()
    try:
        assert entered.wait(2)
        # L13 ponechává cíl nad živým limitem v plánovači, ne ve frontě poolu.
        wait_for(lambda: ctrl.engine._due["2"] <= time.monotonic())
        assert ctrl.engine._inflight == {"1"}
        ctrl.engine.stop(timeout=0.01)
        assert ctrl.engine._inflight == {"1"}
        ctrl.engine.start()
        release.set()
        ctrl.engine.test_now("2")
        wait_for(lambda: ctrl.pinger.calls.get("192.0.2.2", 0) > 0)
    finally:
        release.set()
        ctrl.engine.stop(timeout=1)


def test_stop_deadline_includes_retired_pools(ctrl, monkeypatch):
    """Regrese N1: všechny generace poolu sdílejí jeden timeout stop()."""
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()

    def hang(address, _timeout):
        entered[int(address.rsplit(".", 1)[1]) - 1].set()
        release.wait()
        return PingResult(PingStatus.TIMEOUT)

    monkeypatch.setattr(ctrl.pinger, "ping", hang)
    ctrl.engine.max_concurrent = 1
    ctrl.engine.set_targets([PingTarget(name=str(i), host=f"192.0.2.{i}", id=str(i))
                             for i in (1, 2)])
    ctrl.engine.start()
    pools = []
    try:
        assert entered[0].wait(2)
        ctrl.engine.set_max_concurrent(2)
        assert entered[1].wait(2)
        ctrl.engine.set_max_concurrent(3)
        current = ctrl.engine._executor
        assert current is not None
        pools = [current, *ctrl.engine._retired_executors]
        assert all(isinstance(pool, DaemonPool) for pool in pools)
        started = time.monotonic()
        ctrl.engine.stop(timeout=0.05)
        assert time.monotonic() - started < 0.5
        assert ctrl.engine._inflight == {"1", "2"}
        for pool in pools:
            with pytest.raises(RuntimeError):
                pool.submit(lambda: None)
    finally:
        release.set()
        for pool in pools:
            pool.shutdown(timeout=1)
        ctrl.engine.stop(timeout=1)
