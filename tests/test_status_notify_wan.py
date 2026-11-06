import json

from netmonitor.core.config import NotificationConfig
from netmonitor.core.connection_status import ConnInputs, ConnState, evaluate
from netmonitor.core.monitoring import Transition
from netmonitor.core.notifier import Notifier, fmt_duration
from netmonitor.core.ping_engine import PingStatus
from netmonitor.core.wan_info import WanResult, WanState, fetch_wan_ip, isp_ripestat


# --- stav připojení ---------------------------------------------------------------

def ev(**kw):
    base = dict(iface_up=True, has_default_route=True)
    base.update(kw)
    return evaluate(ConnInputs(**base)).state


def test_online_when_all_up():
    assert ev(gateway_up=True, internet_up=[True, True, True], internet_rtts=[7, 8, 9]) is ConnState.ONLINE


def test_single_failed_server_is_not_internet_outage():
    assert ev(internet_up=[True, True, True, False]) is ConnState.ONLINE
    assert ev(internet_up=[False]) is ConnState.DEGRADED         # jediný cíl => výpadek nelze potvrdit


def test_offline_when_all_internet_targets_down():
    assert ev(internet_up=[False, False, False]) is ConnState.OFFLINE


def test_no_interface_or_route_is_offline():
    assert ev(iface_up=False, internet_up=[True]) is ConnState.OFFLINE
    assert ev(has_default_route=False, internet_up=[True]) is ConnState.OFFLINE


def test_degraded_cases():
    assert ev(internet_up=[True, False, False]) is ConnState.DEGRADED
    assert ev(internet_up=[True, True], internet_rtts=[300, 400]) is ConnState.DEGRADED
    assert ev(internet_up=[True, True], internet_loss=[40, 30]) is ConnState.DEGRADED


def test_icmp_blocked_but_https_ok():
    assert ev(internet_up=[False, False], http_ok=True) is ConnState.DEGRADED


def test_unknown_until_measured():
    assert evaluate(ConnInputs(iface_up=None, has_default_route=None)).state is ConnState.UNKNOWN
    assert ev(internet_up=[None, None]) is ConnState.UNKNOWN


def test_gateway_blocking_icmp_does_not_break_online():
    assert ev(gateway_up=False, internet_up=[True, True]) is ConnState.ONLINE


# --- upozornění -------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def tr(up, tid="a"):
    return Transition(tid, "Router", "10.0.0.1", up, 0, None, 30 if up else None, PingStatus.TIMEOUT)


def test_down_notification_respects_delay():
    clk = Clock()
    n = Notifier(NotificationConfig(delay_s=10), clk)
    n.on_target_transition(tr(False))
    assert n.tick() == []
    clk.t = 11
    out = n.tick()
    assert len(out) == 1 and out[0].kind == "down"
    clk.t = 12
    n.on_target_transition(tr(True))
    out = n.tick()
    assert [x.kind for x in out] == ["up"]


def test_short_outage_is_not_notified_at_all():
    clk = Clock()
    n = Notifier(NotificationConfig(delay_s=10), clk)
    n.on_target_transition(tr(False))
    clk.t = 5
    n.on_target_transition(tr(True))
    clk.t = 20
    assert n.tick() == []


def test_cooldown_prevents_flood():
    clk = Clock()
    n = Notifier(NotificationConfig(delay_s=0, cooldown_s=300), clk)
    sent = []
    for i in range(10):
        clk.t = i * 10
        n.on_target_transition(tr(False))
        sent += n.tick()
        n.on_target_transition(tr(True))
        sent += n.tick()
    downs = [x for x in sent if x.kind == "down"]
    assert len(downs) == 1


def test_internet_and_wan_and_latency():
    clk = Clock()
    n = Notifier(NotificationConfig(delay_s=0, high_latency=True, high_latency_ms=100, high_latency_duration_s=60), clk)
    n.on_internet_state(ConnState.ONLINE)
    n.on_internet_state(ConnState.OFFLINE)
    assert [x.kind for x in n.tick()] == ["internet_down"]
    n.on_internet_state(ConnState.ONLINE)
    assert [x.kind for x in n.tick()] == ["internet_up"]
    n.on_wan_change("1.1.1.1", "2.2.2.2")
    assert n.tick()[0].message == "1.1.1.1 → 2.2.2.2"
    for t in range(0, 70, 2):
        clk.t = t
        n.on_latency("x", "X", 200)
    out = n.tick()
    assert [x.kind for x in out] == ["high_latency"]
    clk.t = 100
    n.on_latency("x", "X", 200)
    assert n.tick() == []                 # neopakuje se, dokud se latence nevrátí


def test_disabled_notifications():
    clk = Clock()
    n = Notifier(NotificationConfig(enabled=False, delay_s=0), clk)
    n.on_target_transition(tr(False))
    assert n.tick() == []


def test_fmt_duration():
    assert fmt_duration(5) == "5 s"
    assert fmt_duration(125) == "2 min 5 s"
    assert fmt_duration(3 * 3600 + 120) == "3 h 2 min"


# --- WAN / ISP --------------------------------------------------------------------

def test_wan_fallback_chain():
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        if "ipify" in url:
            raise OSError("down")
        if "icanhazip" in url:
            return "<html>garbage</html>"
        return "217.170.98.222\n"
    r = fetch_wan_ip(fetch)
    assert r.ip == "217.170.98.222" and "amazonaws" in r.provider
    assert len(calls) == 3


def test_wan_all_fail():
    def fetch(url, timeout):
        raise OSError("offline")
    r = fetch_wan_ip(fetch)
    assert r.ip is None and "offline" in r.error


def test_wan_change_detection_and_persistence(tmp_path):
    f = tmp_path / "wan.json"
    s = WanState(f)
    assert s.update(WanResult("1.1.1.1", ts=1)) is False      # první zjištění není "změna"
    assert s.update(WanResult("1.1.1.1", ts=2)) is False
    assert s.update(WanResult("2.2.2.2", ts=3)) is True
    assert (s.previous, s.current, s.changed_at) == ("1.1.1.1", "2.2.2.2", 3)
    s2 = WanState(f)
    assert (s2.previous, s2.current) == ("1.1.1.1", "2.2.2.2")
    assert s2.update(WanResult(None, error="x", ts=4)) is False and s2.current == "2.2.2.2"


def test_isp_cache_and_provider_fallback(tmp_path):
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        if "ipinfo" in url:
            raise OSError("rate limited")
        if "network-info" in url:
            return json.dumps({"data": {"asns": ["42908"]}})
        return json.dumps({"data": {"holder": "CZ-NORDICTELECOMREGIONAL"}})
    s = WanState(tmp_path / "w.json")
    assert s.isp_for("217.170.98.222", "ipinfo", 3600, fetch) == "AS42908 CZ-NORDICTELECOMREGIONAL"
    n = len(calls)
    assert s.isp_for("217.170.98.222", "ipinfo", 3600, fetch) == "AS42908 CZ-NORDICTELECOMREGIONAL"
    assert len(calls) == n            # z cache


def test_ripestat_parsing():
    def fetch(url, timeout):
        return json.dumps({"data": {"asns": []}})
    assert isp_ripestat("1.2.3.4", fetch) is None
