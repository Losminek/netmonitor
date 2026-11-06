import threading
import time

from netmonitor.core.config import PingTarget
from netmonitor.core.monitoring import MonitorEngine, TargetStats
from netmonitor.core.ping_engine import PingResult, PingStatus, Resolver

from .fakes import FakePinger


def ok(ms=5.0, ts=None):
    return PingResult(PingStatus.OK, rtt_ms=ms, ts=ts or time.time())


def fail(st=PingStatus.TIMEOUT, ts=None):
    return PingResult(st, ts=ts or time.time())


def test_packet_loss_and_stats():
    s = TargetStats(PingTarget(name="x", host="1.1.1.1", retries=3))
    for r in [ok(10), ok(20), fail(), ok(30), fail()]:
        s.add(r)
    snap = s.snapshot()
    assert snap.ok_count == 3 and snap.fail_count == 2
    assert snap.loss_pct == 40.0 and snap.loss_pct_total == 40.0
    assert (snap.rtt_min, snap.rtt_max, snap.rtt_avg) == (10, 30, 20)


def test_loss_window_is_rolling():
    s = TargetStats(PingTarget(name="x", host="1.1.1.1"))
    for _ in range(100):
        s.add(fail())
    for _ in range(100):
        s.add(ok())
    snap = s.snapshot()
    assert snap.loss_pct == 0.0          # klouzavé okno 100 vzorků
    assert snap.loss_pct_total == 50.0   # celkově od startu


def test_offline_online_transitions_with_retries():
    s = TargetStats(PingTarget(name="x", host="1.1.1.1", retries=3))
    assert s.add(ok(ts=100)) is None
    assert s.up is True
    assert s.add(fail(ts=102)) is None      # 1. selhání – ještě ne výpadek
    assert s.add(fail(ts=104)) is None      # 2.
    tr = s.add(fail(ts=106))                # 3. => výpadek
    assert tr is not None and tr.up is False
    assert tr.outage_start_ts == 102        # začátek = první selhání
    assert s.snapshot().outage_count == 1
    assert s.add(fail(ts=108)) is None      # žádné opakované vyhlášení
    back = s.add(ok(ts=110))
    assert back is not None and back.up is True
    assert back.duration_s == 8
    snap = s.snapshot()
    assert snap.outage_start_ts is None and snap.last_outage_duration_s == 8
    assert snap.last_success_ts == 110


def test_single_failure_does_not_flap():
    s = TargetStats(PingTarget(name="x", host="1.1.1.1", retries=3))
    s.add(ok())
    for _ in range(5):
        assert s.add(fail()) is None
        assert s.add(ok()) is None
    assert s.snapshot().outage_count == 0


def test_never_reachable_target_goes_down():
    s = TargetStats(PingTarget(name="x", host="1.1.1.1", retries=2))
    assert s.add(fail()) is None
    tr = s.add(fail())
    assert tr and tr.up is False


def _engine(pinger, **kw):
    results, transitions = [], []
    lock = threading.Lock()

    def on_r(snap, r):
        with lock:
            results.append((snap.target_id, r))
    eng = MonitorEngine(pinger, Resolver(), on_result=on_r, on_transition=transitions.append, **kw)
    return eng, results, transitions


def test_parallel_slow_target_does_not_block_others():
    pinger = FakePinger({"10.9.9.9": "slow:2.0", "10.1.1.1": "ok:1"})
    eng, results, _ = _engine(pinger, max_concurrent=4)
    eng.set_targets([PingTarget(name="slow", host="10.9.9.9", interval_s=1, timeout_s=2.0, id="s"),
                     PingTarget(name="fast", host="10.1.1.1", interval_s=1, timeout_s=1.0, id="f")])
    eng.start()
    time.sleep(3.2)
    eng.stop()
    fast = [r for tid, r in results if tid == "f"]
    assert len(fast) >= 3, "rychlý cíl musí být měřen nezávisle na pomalém"
    assert pinger.max_inflight.get("10.9.9.9", 0) == 1, "na jeden cíl nesmí běžet souběžné požadavky"


def test_engine_interval_and_success():
    pinger = FakePinger(default="ok:3")
    eng, results, _ = _engine(pinger)
    eng.set_targets([PingTarget(name=f"t{i}", host=f"10.0.0.{i}", interval_s=1, id=str(i)) for i in range(10)])
    eng.start()
    time.sleep(2.3)
    eng.stop()
    per = {}
    for tid, r in results:
        per[tid] = per.get(tid, 0) + 1
        assert r.ok and r.rtt_ms == 3
    assert len(per) == 10
    assert all(2 <= n <= 4 for n in per.values()), per


def test_engine_detects_outage_and_recovery():
    state = {"mode": "timeout"}
    pinger = FakePinger({"10.0.0.5": lambda: "ok:2" if state["mode"] == "ok" else "timeout"})
    eng, _, transitions = _engine(pinger)
    eng.set_targets([PingTarget(name="dev", host="10.0.0.5", interval_s=1, timeout_s=0.2, retries=2, id="d")])
    eng.start()
    time.sleep(2.4)
    state["mode"] = "ok"
    time.sleep(1.5)
    eng.stop()
    ups = [t.up for t in transitions]
    assert ups[:2] == [False, True], transitions


def test_test_now_and_disabled_targets():
    pinger = FakePinger(default="ok:1")
    eng, results, _ = _engine(pinger)
    eng.set_targets([PingTarget(name="a", host="10.0.0.1", interval_s=60, id="a"),
                     PingTarget(name="b", host="10.0.0.2", interval_s=1, enabled=False, id="b")])
    eng.start()
    time.sleep(0.3)
    n0 = len(results)
    assert eng.test_now("a")
    assert not eng.test_now("b")            # vypnutý cíl se neměří
    time.sleep(0.3)
    eng.stop()
    assert len(results) == n0 + 1
    assert all(tid == "a" for tid, _ in results)


def test_set_targets_keeps_stats_unless_host_changes():
    pinger = FakePinger(default="ok:1")
    eng, _, _ = _engine(pinger)
    t = PingTarget(name="a", host="10.0.0.1", interval_s=1, id="a")
    eng.set_targets([t])
    eng.start()
    time.sleep(1.2)
    before = eng.snapshot("a").ok_count
    eng.set_targets([PingTarget(name="renamed", host="10.0.0.1", interval_s=1, id="a")])
    assert eng.snapshot("a").ok_count == before
    eng.set_targets([PingTarget(name="renamed", host="10.0.0.2", interval_s=1, id="a")])
    assert eng.snapshot("a").ok_count == 0
    eng.stop()


def test_clean_shutdown_no_leftover_threads():
    pinger = FakePinger(default="slow:0.5")
    eng, _, _ = _engine(pinger, max_concurrent=8)
    eng.set_targets([PingTarget(name=f"t{i}", host=f"10.0.1.{i}", interval_s=1, timeout_s=0.5, id=str(i))
                     for i in range(8)])
    eng.start()
    time.sleep(1.1)
    t0 = time.monotonic()
    eng.stop(timeout=5)
    assert time.monotonic() - t0 < 3
    time.sleep(0.1)
    alive = [t.name for t in threading.enumerate() if t.name.startswith(("ping", "ping-scheduler"))]
    assert alive == [], alive
    assert not eng.running


def test_stop_is_idempotent_and_restartable():
    eng, results, _ = _engine(FakePinger(default="ok:1"))
    eng.set_targets([PingTarget(name="a", host="10.0.0.1", interval_s=1, id="a")])
    eng.start()
    eng.stop()
    eng.stop()
    eng.start()
    time.sleep(0.4)
    eng.stop()
    assert results
