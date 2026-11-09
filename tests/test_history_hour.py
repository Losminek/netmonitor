"""Regrese B08: hodinový graf kombinuje trvalou historii a detailní vzorky."""
import pytest

from netmonitor.core.history import HistoryStore, SeriesPoint
from netmonitor.core.ping_engine import PingResult, PingStatus


BASE = 1_800_000_000  # začátek minuty, syntetický čas


def record(store, ts, ms):
    store.record("t", PingResult(PingStatus.OK if ms is not None else PingStatus.TIMEOUT,
                                rtt_ms=ms, ts=ts))


def assert_summary(points, summary):
    assert sum(p.n_ok for p in points) == summary["n_ok"]
    assert sum(p.n_fail for p in points) == summary["n_fail"]
    values = [p for p in points if p.rtt_avg is not None]
    if values:
        assert min(p.rtt_min for p in values) == summary["rtt_min"]
        assert max(p.rtt_max for p in values) == summary["rtt_max"]
        assert sum(p.rtt_avg * p.n_ok for p in values) / summary["n_ok"] == pytest.approx(summary["rtt_avg"])


def test_hour_data_keeps_history_after_restart(tmp_path):
    """Regrese B08: bod před 30 minutami nezmizí při příchodu dvou raw bodů."""
    path = tmp_path / "history.db"
    old = HistoryStore(path)
    record(old, BASE - 1800 + 10, 40.0)
    old.close()
    store = HistoryStore(path)
    try:
        record(store, BASE + 10, 10.0)
        record(store, BASE + 12, 20.0)
        points, bucket, summary = store.hour_data("t", BASE - 3600)
        assert [p.ts for p in points] == [BASE - 1800, BASE + 10, BASE + 12]
        assert [p.rtt_avg for p in points] == [40.0, 10.0, 20.0]
        assert bucket == 2.0
        assert summary == store.summary("t", BASE - 3600)
        assert summary["n_ok"] == 3
        assert_summary(points, summary)
    finally:
        store.close()


def test_hour_data_preserves_partial_boundary_minute(tmp_path):
    """Regrese B08: DB zahrne celou minutu restartu, raw ji nesmí započíst podruhé."""
    path = tmp_path / "history.db"
    old = HistoryStore(path)
    record(old, BASE + 5, 50.0)
    old.close()
    store = HistoryStore(path)
    try:
        record(store, BASE + 10, 10.0)
        record(store, BASE + 12, None)
        record(store, BASE + 61, 20.0)
        record(store, BASE + 63, 30.0)
        points, bucket, summary = store.hour_data("t", BASE - 3600)
        assert points == [SeriesPoint(BASE, 10.0, 30.0, 50.0, 2, 1),
                          SeriesPoint(BASE + 61, 20.0, 20.0, 20.0, 1, 0),
                          SeriesPoint(BASE + 63, 30.0, 30.0, 30.0, 1, 0)]
        assert bucket == 2.0
        assert summary == store.summary("t", BASE - 3600)
        assert_summary(points, summary)
    finally:
        store.close()


@pytest.mark.parametrize("count", [0, 1, 2])
def test_hour_data_empty_or_complete_raw_minute(tmp_path, count):
    """Regrese B08: úplná minuta z RAM zachová i samostatný raw bod a chyby."""
    store = HistoryStore(tmp_path / "history.db")
    try:
        for i in range(count):
            record(store, BASE + 10 + i * 2, None if i else 10.0)
        points, bucket, summary = store.hour_data("t", BASE - 3600)
        assert [p.ts for p in points] == [BASE + 10 + i * 2 for i in range(count)]
        assert bucket == (2.0 if count == 2 else 60.0)
        assert summary == store.summary("t", BASE - 3600)
        assert_summary(points, summary)
    finally:
        store.close()


def test_hour_data_uses_same_period_as_summary(tmp_path):
    """Regrese B08: neúplná počáteční minuta období má stejný filtr jako souhrn."""
    store = HistoryStore(tmp_path / "history.db")
    try:
        record(store, BASE + 12, 99.0)
        record(store, BASE + 61, 10.0)
        record(store, BASE + 63, 20.0)
        points, _, summary = store.hour_data("t", BASE + 10.5)
        assert [p.ts for p in points] == [BASE + 61, BASE + 63]
        assert summary == store.summary("t", BASE + 10.5)
        assert_summary(points, summary)
    finally:
        store.close()


def test_hour_data_needs_one_select(tmp_path):
    """Regrese B16: série i souhrn se získají jediným čtecím SQL dotazem."""
    store = HistoryStore(tmp_path / "history.db")
    try:
        record(store, BASE + 10, 10.0)
        record(store, BASE + 12, 20.0)
        queries = []
        store._conn.set_trace_callback(queries.append)
        store.hour_data("t", BASE - 3600)
        assert len([q for q in queries if q.lstrip().upper().startswith("SELECT")]) == 1
    finally:
        store.close()


@pytest.mark.parametrize("old_offset", [-1800, 0])
def test_hour_dialog_keeps_persisted_data(tmp_path, monkeypatch, old_offset):
    """Regrese B08: skutečný dialog použije spojenou sérii i odpovídající souhrn."""
    from types import SimpleNamespace
    from typing import cast

    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from netmonitor.core.config import AppConfig, PingTarget
    from netmonitor.gui.controller import Controller
    from netmonitor.gui.history_dialog import HistoryDialog

    class Signals(QObject):
        targets_changed = Signal()

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "history.db"
    old = HistoryStore(path)
    record(old, BASE + old_offset + 5, 40.0)
    old.close()
    store = HistoryStore(path)
    dialog = None
    try:
        record(store, BASE + 10, 10.0)
        record(store, BASE + 12, 20.0)
        cfg = AppConfig()
        cfg.ping.targets = [PingTarget(id="t", name="Test", host="192.0.2.1")]
        signals = Signals()
        ctrl = cast(Controller, SimpleNamespace(cfg=cfg, history=store, snapshots={},
                                                targets_changed=signals.targets_changed))
        monkeypatch.setattr("netmonitor.gui.history_dialog.time.time", lambda: BASE + 20)
        dialog = HistoryDialog(ctrl)
        queries = []
        store._conn.set_trace_callback(queries.append)
        dialog.refresh()
        assert dialog.chart.points[0].ts == BASE + old_offset
        assert sum(p.n_ok for p in dialog.chart.points) == 3
        assert dialog.stat_labels["ok"].text() == "3"
        # Série + cílové a globální události; souhrn už nepotřebuje vlastní SELECT.
        assert len([q for q in queries if q.lstrip().upper().startswith("SELECT")]) == 3
        assert app is not None
    finally:
        if dialog is not None:
            dialog.close()
        store.close()
