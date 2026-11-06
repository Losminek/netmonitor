import csv
import sqlite3
import time

from netmonitor.core.history import MIGRATIONS, SCHEMA_VERSION, HistoryStore
from netmonitor.core.ping_engine import PingResult, PingStatus


def r(ok=True, ms=5.0, ts=None, st=PingStatus.TIMEOUT):
    ts = ts or time.time()
    return PingResult(PingStatus.OK, rtt_ms=ms, ts=ts) if ok else PingResult(st, ts=ts, error="x")


def test_record_aggregates_per_minute_and_flushes(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    base = (int(time.time()) // 60) * 60 - 600
    for i, ms in enumerate([10, 20, 30]):
        h.record("t1", r(ms=ms, ts=base + i))
    h.record("t1", r(False, ts=base + 5))
    assert h.flush() == 1                       # jedna minutová buňka = jeden řádek
    pts = h.series("t1", base - 1, 60)
    assert len(pts) == 1
    p = pts[0]
    assert (p.n_ok, p.n_fail, p.rtt_min, p.rtt_max) == (3, 1, 10, 30)
    assert abs(p.rtt_avg - 20) < 1e-9 and p.loss_pct == 25.0
    h.close()


def test_upsert_merges_partial_minutes(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    base = (int(time.time()) // 60) * 60 - 300
    h.record("t", r(ms=10, ts=base + 1))
    h.flush()
    h.record("t", r(ms=30, ts=base + 2))
    h.record("t", r(False, ts=base + 3))
    h.flush()
    p = h.series("t", base - 1)[0]
    assert (p.n_ok, p.n_fail, p.rtt_min, p.rtt_max, p.rtt_avg) == (2, 1, 10, 30, 20)
    s = h.summary("t", base - 1)
    assert s["loss_pct"] == 100 / 3
    h.close()


def test_bucketing_for_long_ranges(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    base = (int(time.time()) // 3600) * 3600 - 7200
    for m in range(120):                      # 2 hodiny po minutách
        h.record("t", r(ms=m, ts=base + m * 60))
    h.flush()
    pts = h.series("t", base, 3600)
    assert len(pts) == 2
    assert pts[0].n_ok == 60 and pts[0].rtt_min == 0 and pts[0].rtt_max == 59
    h.close()


def test_events_are_separate_and_queryable(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    h.add_event("down", "t1", "timeout", None, None, ts=1000)
    h.add_event("up", "t1", "ok", None, 30, ts=1030)
    h.add_event("wan_change", None, None, "1.1.1.1 -> 2.2.2.2", ts=1040)
    ev = h.events(0, "t1")
    assert [e.kind for e in ev] == ["up", "down"]
    assert ev[0].duration_s == 30
    assert len(h.events(0)) == 3
    h.close()


def test_purge_retention(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    old = time.time() - 40 * 86400
    h.record("t", r(ts=old))
    h.record("t", r())
    h.add_event("down", "t", ts=old)
    h.flush()
    n = h.purge(30)
    assert n == 2
    assert len(h.series("t", 0)) == 1
    h.close()


def test_raw_buffer_for_one_hour_graph(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    now = time.time()
    h.record("t", r(ms=1, ts=now - 4000))
    h.record("t", r(ms=2, ts=now - 10))
    h.record("t", r(False, ts=now - 5))
    raw = h.raw_series("t", now - 3600)
    assert [x[1] for x in raw] == [2, None]
    h.close()


def test_export_csv(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    h.record("t", r(ms=7.5))
    out = tmp_path / "out.csv"
    n = h.export_csv(out, 0, names={"t": "Zelina"})
    assert n == 1
    rows = list(csv.reader(open(out, encoding="utf-8-sig"), delimiter=";"))
    assert rows[0][0] == "cas_utc" and rows[1][3] == "Zelina" and rows[1][8] == "7.50"
    ev = tmp_path / "ev.csv"
    h.add_event("down", "t")
    assert h.export_events_csv(ev, 0, {"t": "Zelina"}) == 1
    h.close()


def test_migration_from_v1(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO samples_1m(target_id, ts, n_ok, n_fail) VALUES ('t', 60, 1, 0)")
    conn.commit()
    conn.close()
    h = HistoryStore(db)
    assert h.version == SCHEMA_VERSION == 2
    names = {r[0] for r in h._conn.execute("SELECT name FROM sqlite_master")}
    assert {"targets", "idx_events_ts", "idx_samples_ts"} <= names
    assert h.series("t", 0)[0].n_ok == 1          # data přežila migraci
    h.close()


def test_disabled_history_records_nothing(tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    h.enabled = False
    h.record("t", r())
    assert h.flush() == 0
    h.close()
