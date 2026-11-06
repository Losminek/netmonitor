import shutil
import socket
import sys

import pytest

from netmonitor.core.ping_engine import (PingResult, PingStatus, Resolver, SocketIcmpPinger, SystemPinger,
                                         parse_ping_output, ping_host, select_pinger)

from .fakes import FakePinger

# --- parsování výstupu systémového ping (lokalizace, chybové stavy) ---------------

LINUX_OK = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=7.12 ms

--- 8.8.8.8 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
rtt min/avg/max/mdev = 7.120/7.120/7.120/0.000 ms"""
LINUX_TIMEOUT = """PING 192.0.2.1 (192.0.2.1) 56(84) bytes of data.

--- 192.0.2.1 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms"""
LINUX_UNREACH = """PING 192.168.9.9 (192.168.9.9) 56(84) bytes of data.
From 192.168.1.10 icmp_seq=1 Destination Host Unreachable

--- 192.168.9.9 ping statistics ---
1 packets transmitted, 0 received, +1 errors, 100% packet loss, time 0ms"""
LINUX_NET = "ping: connect: Network is unreachable"
WIN_EN_OK = """Pinging 8.8.8.8 with 32 bytes of data:
Reply from 8.8.8.8: bytes=32 time=7ms TTL=117

Ping statistics for 8.8.8.8:
    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),"""
WIN_EN_SUBMS = "Reply from 192.168.1.1: bytes=32 time<1ms TTL=64"
WIN_CZ_OK = """Příkaz PING na 8.8.8.8 s délkou 32 bajtů:
Odpověď od 8.8.8.8: bajty=32 čas=6ms TTL=117

Statistika ping pro 8.8.8.8:
    Pakety: Odeslané = 1, Přijaté = 1, Ztracené = 0 (ztráta 0%),"""
WIN_CZ_TIMEOUT = """Příkaz PING na 10.0.0.138 s délkou 32 bajtů:
Vypršel časový limit žádosti.

Statistika ping pro 10.0.0.138:
    Pakety: Odeslané = 1, Přijaté = 0, Ztracené = 1 (ztráta 100%),"""
WIN_EN_UNREACH = """Pinging 10.0.0.138 with 32 bytes of data:
Reply from 192.168.1.10: Destination host unreachable.

Ping statistics for 10.0.0.138:
    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),"""
MAC_OK = """PING 8.8.8.8 (8.8.8.8): 56 data bytes
64 bytes from 8.8.8.8: icmp_seq=0 ttl=117 time=7.312 ms"""


@pytest.mark.parametrize("text,rc,plat,status,rtt", [
    (LINUX_OK, 0, "linux", PingStatus.OK, 7.12),
    (LINUX_TIMEOUT, 1, "linux", PingStatus.TIMEOUT, None),
    (LINUX_UNREACH, 1, "linux", PingStatus.HOST_UNREACHABLE, None),
    (LINUX_NET, 2, "linux", PingStatus.NET_UNREACHABLE, None),
    (WIN_EN_OK, 0, "win32", PingStatus.OK, 7.0),
    (WIN_EN_SUBMS, 0, "win32", PingStatus.OK, 1.0),
    (WIN_CZ_OK, 0, "win32", PingStatus.OK, 6.0),
    (WIN_CZ_TIMEOUT, 1, "win32", PingStatus.TIMEOUT, None),
    (WIN_EN_UNREACH, 0, "win32", PingStatus.HOST_UNREACHABLE, None),   # Windows vrací rc=0!
    (MAC_OK, 0, "darwin", PingStatus.OK, 7.312),
])
def test_parse_ping_output(text, rc, plat, status, rtt):
    st, val = parse_ping_output(text, rc, plat)
    assert st is status
    assert val == rtt


def test_timeout_is_never_a_latency():
    st, val = parse_ping_output("Request timed out.", 1, "win32")
    assert st is PingStatus.TIMEOUT and val is None


@pytest.mark.parametrize("plat,expected", [
    ("win32", ["-n", "1", "-w", "1500", "-4", "8.8.8.8"]),
    ("linux", ["-n", "-c", "1", "-W", "2", "8.8.8.8"]),
    ("darwin", ["-n", "-c", "1", "-W", "1500", "8.8.8.8"]),
])
def test_system_ping_args_no_shell(plat, expected):
    args = SystemPinger("ping").build_args("8.8.8.8", 1.5, plat)
    assert args[1:] == expected
    assert all(isinstance(a, str) for a in args)


def test_system_ping_args_reject_injection_shape():
    # adresa je vždy jediný argument => i podivný text nemůže vytvořit další příkaz
    args = SystemPinger("ping").build_args("8.8.8.8; rm -rf /", 1, "linux")
    assert args[-1] == "8.8.8.8; rm -rf /" and len(args) == 7


# --- resolver --------------------------------------------------------------------

def test_resolver_ip_literal():
    assert Resolver().resolve("8.8.8.8") == ("8.8.8.8", "")
    assert Resolver().resolve("[::1]")[0] == "::1"


def test_unresolvable_domain_gives_dns_error():
    r = ping_host("does-not-exist.invalid", 0.5, FakePinger(), Resolver())
    assert r.status is PingStatus.DNS_ERROR
    assert r.rtt_ms is None


def test_ping_host_never_raises():
    class Boom(FakePinger):
        def ping(self, address, timeout):
            raise RuntimeError("boom")
    r = ping_host("127.0.0.1", 0.5, Boom(), Resolver())
    assert r.status is PingStatus.ERROR and "boom" in r.error


# --- skutečné ICMP (integrační; přeskočí se, kde to prostředí nedovolí) -----------

def _real_pinger():
    p = SocketIcmpPinger.probe() if sys.platform != "win32" else None
    if p:
        return p
    if shutil.which("ping"):
        return SystemPinger()
    pytest.skip("Žádná ICMP implementace k dispozici")


@pytest.mark.integration
def test_real_ping_localhost_success():
    r = ping_host("127.0.0.1", 1.0, _real_pinger(), Resolver())
    assert r.ok, r
    assert r.rtt_ms is not None and 0 <= r.rtt_ms < 1000


@pytest.mark.integration
def test_real_ping_timeout_testnet():
    # 192.0.2.0/24 (TEST-NET-1) nikdy neodpovídá
    r = ping_host("192.0.2.123", 0.6, _real_pinger(), Resolver())
    assert r.status in (PingStatus.TIMEOUT, PingStatus.HOST_UNREACHABLE, PingStatus.NET_UNREACHABLE)
    assert r.rtt_ms is None


@pytest.mark.integration
def test_select_pinger_works():
    p = select_pinger()
    r = p.ping("127.0.0.1", 1.0)
    assert isinstance(r, PingResult)
