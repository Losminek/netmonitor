"""ICMP echo – platformně nezávislé rozhraní a tři implementace.

Pořadí výběru (``select_pinger("auto")``):

* **Windows**: ``IcmpSendEcho`` z iphlpapi.dll přes ctypes – oficiální API,
  nevyžaduje administrátorská práva, výstup nezávislý na lokalizaci.
* **Linux / macOS**: nepřivilegovaný ICMP ``SOCK_DGRAM`` socket
  (Linux: povoleno přes ``net.ipv4.ping_group_range``; macOS: vždy),
  případně ``SOCK_RAW`` při běhu s oprávněním.
* **Fallback**: systémový ``ping`` spouštěný bez shellu (seznam argumentů),
  s ``LC_ALL=C`` a tvrdým timeoutem procesu (žádné zombie procesy).

Timeout je vždy samostatný stav – nikdy se nevrací "latence 30000 ms".
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import secrets
import select
import socket
import struct
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)


class PingStatus(str, Enum):
    OK = "ok"
    TIMEOUT = "timeout"
    HOST_UNREACHABLE = "host_unreachable"
    NET_UNREACHABLE = "net_unreachable"
    DNS_ERROR = "dns_error"
    ERROR = "error"

    @property
    def is_ok(self) -> bool:
        return self is PingStatus.OK


@dataclass(frozen=True)
class PingResult:
    status: PingStatus
    rtt_ms: float | None = None
    address: str | None = None
    error: str = ""
    ts: float = field(default_factory=time.time)   # wall-clock čas měření (pro historii)

    @property
    def ok(self) -> bool:
        return self.status is PingStatus.OK


# ---------------------------------------------------------------------------
# DNS resolver s cache
# ---------------------------------------------------------------------------

class Resolver:
    """Překlad domén s krátkou cache (pozitivní 60 s, negativní 10 s)."""

    def __init__(self, ttl: float = 60.0, negative_ttl: float = 10.0):
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self._cache: dict[tuple[str, int], tuple[float, str | None, str]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def resolve(self, host: str, prefer_v6: bool = False) -> tuple[str | None, str]:
        """Vrátí (ip, chyba). Pro IP literál vrací přímo adresu."""
        h = host.strip().strip("[]")
        try:
            return str(ipaddress.ip_address(h)), ""
        except ValueError:
            pass
        fam = socket.AF_INET6 if prefer_v6 else socket.AF_INET
        key = (h.lower(), fam)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1], hit[2]
        try:
            infos = socket.getaddrinfo(h, None, fam, socket.SOCK_DGRAM)
            ip = infos[0][4][0] if infos else None
            err = "" if ip else "Žádná adresa"
        except socket.gaierror as e:
            ip, err = None, f"DNS: {e.strerror or e}"
        except (UnicodeError, OSError) as e:
            ip, err = None, f"DNS: {e}"
        exp = now + (self.ttl if ip else self.negative_ttl)
        with self._lock:
            self._cache[key] = (exp, ip, err)
        return ip, err


# ---------------------------------------------------------------------------
# Rozhraní
# ---------------------------------------------------------------------------

class Pinger(ABC):
    name = "abstract"

    @abstractmethod
    def ping(self, address: str, timeout: float) -> PingResult:
        """Jeden ICMP echo request na již přeloženou IP adresu."""

    def close(self) -> None:  # pragma: no cover - default noop
        pass


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


# ---------------------------------------------------------------------------
# Socket implementace (Linux/macOS)
# ---------------------------------------------------------------------------

class SocketIcmpPinger(Pinger):
    """ICMP přes SOCK_DGRAM (bez práv) nebo SOCK_RAW (root/CAP_NET_RAW)."""

    IP_RECVERR = 11
    IPV6_RECVERR = 25

    def __init__(self, sock_type: int):
        self.sock_type = sock_type
        self.name = "icmp-dgram" if sock_type == socket.SOCK_DGRAM else "icmp-raw"
        self._seq = 0
        self._lock = threading.Lock()
        self._ident = os.getpid() & 0xFFFF

    @classmethod
    def probe(cls) -> "SocketIcmpPinger | None":
        for st in (socket.SOCK_DGRAM, socket.SOCK_RAW):
            try:
                s = socket.socket(socket.AF_INET, st, socket.IPPROTO_ICMP)
                s.close()
                p = cls(st)
                # ověř funkčnost na loopbacku (zachytí např. zakázaný ping_group_range)
                r = p.ping("127.0.0.1", 0.5)
                if r.ok:
                    return p
                log.debug("ICMP %s socket nefunkční: %s", st, r)
            except (PermissionError, OSError) as e:
                log.debug("ICMP socket typu %s nedostupný: %s", st, e)
        return None

    def _next_seq(self) -> int:
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFF
            return self._seq

    def ping(self, address: str, timeout: float) -> PingResult:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return PingResult(PingStatus.ERROR, error=f"Neplatná adresa {address}")
        v6 = ip.version == 6
        fam = socket.AF_INET6 if v6 else socket.AF_INET
        proto = socket.IPPROTO_ICMPV6 if v6 else socket.IPPROTO_ICMP
        req_type, rep_type, unreach_type = (128, 129, 1) if v6 else (8, 0, 3)
        seq = self._next_seq()
        token = secrets.token_bytes(8)
        payload = token + b"NetMonitor-icmp!"
        header = struct.pack("!BBHHH", req_type, 0, 0, self._ident, seq)
        if not v6:
            header = struct.pack("!BBHHH", req_type, 0, _checksum(header + payload), self._ident, seq)
        packet = header + payload

        try:
            sock = socket.socket(fam, self.sock_type, proto)
        except OSError as e:
            return PingResult(PingStatus.ERROR, address=address, error=str(e))
        try:
            if sys.platform.startswith("linux"):
                try:
                    if v6:
                        sock.setsockopt(socket.IPPROTO_IPV6, self.IPV6_RECVERR, 1)
                    else:
                        sock.setsockopt(socket.SOL_IP, self.IP_RECVERR, 1)
                except OSError:
                    pass
            sock.setblocking(False)
            dest = (address, 0, 0, 0) if v6 else (address, 0)
            try:
                if self.sock_type == socket.SOCK_DGRAM:
                    sock.connect(dest)
                    t0 = time.perf_counter()
                    sock.send(packet)
                else:
                    t0 = time.perf_counter()
                    sock.sendto(packet, dest)
            except OSError as e:
                return _oserror_result(e, address)
            deadline = t0 + timeout
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return PingResult(PingStatus.TIMEOUT, address=address)
                r, _, _ = select.select([sock], [], [], remaining)
                if not r:
                    return PingResult(PingStatus.TIMEOUT, address=address)
                try:
                    data, src = sock.recvfrom(2048)
                except BlockingIOError:
                    continue
                except OSError as e:  # ICMP chyba doručená přes IP_RECVERR
                    return _oserror_result(e, address)
                t1 = time.perf_counter()
                # macOS DGRAM a všechny RAW IPv4 sockety vrací i IP hlavičku
                if not v6 and data and (data[0] >> 4) == 4 and len(data) >= 20:
                    data = data[(data[0] & 0x0F) * 4:]
                if len(data) < 8:
                    continue
                itype, icode = data[0], data[1]
                if itype == rep_type and token in data[8:]:
                    return PingResult(PingStatus.OK, rtt_ms=(t1 - t0) * 1000.0, address=address)
                if itype == unreach_type and token in data:
                    st = PingStatus.NET_UNREACHABLE if icode == 0 else PingStatus.HOST_UNREACHABLE
                    return PingResult(st, address=address, error=f"ICMP unreachable code {icode}")
                # cizí paket (RAW socket vidí veškeré ICMP) – čekáme dál
        finally:
            sock.close()


def _oserror_result(e: OSError, address: str) -> PingResult:
    import errno
    if e.errno in (errno.EHOSTUNREACH, getattr(errno, "EHOSTDOWN", -1)):
        return PingResult(PingStatus.HOST_UNREACHABLE, address=address, error=e.strerror or str(e))
    if e.errno in (errno.ENETUNREACH, errno.ENETDOWN):
        return PingResult(PingStatus.NET_UNREACHABLE, address=address, error=e.strerror or str(e))
    if e.errno == errno.ECONNREFUSED:  # ICMP port/proto unreachable
        return PingResult(PingStatus.HOST_UNREACHABLE, address=address, error=e.strerror or str(e))
    return PingResult(PingStatus.ERROR, address=address, error=e.strerror or str(e))


# ---------------------------------------------------------------------------
# Windows: IcmpSendEcho (iphlpapi) – bez admin práv, nezávislé na jazyku
# ---------------------------------------------------------------------------

class WindowsIcmpPinger(Pinger):
    name = "win-icmpapi"
    IP_SUCCESS = 0
    STATUS_MAP = {
        11002: PingStatus.NET_UNREACHABLE,    # IP_DEST_NET_UNREACHABLE
        11003: PingStatus.HOST_UNREACHABLE,   # IP_DEST_HOST_UNREACHABLE
        11004: PingStatus.HOST_UNREACHABLE,   # IP_DEST_PROT_UNREACHABLE
        11005: PingStatus.HOST_UNREACHABLE,   # IP_DEST_PORT_UNREACHABLE
        11010: PingStatus.TIMEOUT,            # IP_REQ_TIMED_OUT
        11013: PingStatus.TIMEOUT,            # IP_TTL_EXPIRED_TRANSIT
        11050: PingStatus.ERROR,              # IP_GENERAL_FAILURE
        1231: PingStatus.NET_UNREACHABLE,     # ERROR_NETWORK_UNREACHABLE
        1232: PingStatus.HOST_UNREACHABLE,    # ERROR_HOST_UNREACHABLE
    }

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        lib = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

        class IP_OPTION_INFORMATION(ctypes.Structure):
            _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                        ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]

        class ICMP_ECHO_REPLY(ctypes.Structure):
            _fields_ = [("Address", ctypes.c_uint32), ("Status", ctypes.c_uint32),
                        ("RoundTripTime", ctypes.c_uint32), ("DataSize", ctypes.c_uint16),
                        ("Reserved", ctypes.c_uint16), ("Data", ctypes.c_void_p),
                        ("Options", IP_OPTION_INFORMATION)]

        self._Reply = ICMP_ECHO_REPLY
        self._create = lib.IcmpCreateFile
        self._create.restype = wintypes.HANDLE
        self._create.argtypes = []
        self._close = lib.IcmpCloseHandle
        self._close.argtypes = [wintypes.HANDLE]
        self._close.restype = wintypes.BOOL
        self._send = lib.IcmpSendEcho
        self._send.argtypes = [wintypes.HANDLE, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint16,
                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        self._send.restype = ctypes.c_uint32
        self._invalid = ctypes.c_void_p(-1).value
        self._v6_fallback: SystemPinger | None = None

    def ping(self, address: str, timeout: float) -> PingResult:
        ct = self._ct
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return PingResult(PingStatus.ERROR, error=f"Neplatná adresa {address}")
        if ip.version == 6:  # IPv6 zatím přes systémový ping (Icmp6SendEcho2 – budoucí rozšíření)
            if self._v6_fallback is None:
                self._v6_fallback = SystemPinger()
            return self._v6_fallback.ping(address, timeout)
        handle = self._create()
        if not handle or handle == self._invalid:
            return PingResult(PingStatus.ERROR, address=address, error=f"IcmpCreateFile {ct.get_last_error()}")
        try:
            payload = b"NetMonitor-icmp-probe-0123456789"
            req = ct.create_string_buffer(payload, len(payload))
            reply_size = ct.sizeof(self._Reply) + len(payload) + 8 + 64
            buf = ct.create_string_buffer(reply_size)
            dest = int.from_bytes(ip.packed, "little")
            tmo = max(1, int(timeout * 1000))
            t0 = time.perf_counter()
            n = self._send(handle, dest, req, len(payload), None, buf, reply_size, tmo)
            measured = (time.perf_counter() - t0) * 1000.0
            if n == 0:
                err = ct.get_last_error()
                st = self.STATUS_MAP.get(err, PingStatus.TIMEOUT if measured >= tmo * 0.95 else PingStatus.ERROR)
                return PingResult(st, address=address, error=f"code {err}")
            rep = self._Reply.from_buffer(buf)
            if rep.Status != self.IP_SUCCESS:
                st = self.STATUS_MAP.get(rep.Status, PingStatus.ERROR)
                return PingResult(st, address=address, error=f"status {rep.Status}")
            reported = float(rep.RoundTripTime)   # rozlišení API je 1 ms
            # měřený čas dává sub-ms přesnost; použijeme ho, pokud odpovídá hlášené hodnotě
            rtt = measured if reported <= measured <= reported + 1.5 else reported
            return PingResult(PingStatus.OK, rtt_ms=rtt, address=address)
        finally:
            self._close(handle)


# ---------------------------------------------------------------------------
# Systémový ping (fallback) – bez shellu, s timeoutem procesu
# ---------------------------------------------------------------------------

_RTT_RE = re.compile(r"[=<]\s*(\d+(?:[.,]\d+)?)\s*ms", re.IGNORECASE)
_TTL_RE = re.compile(r"\b(?:ttl|hlim)[=\s]", re.IGNORECASE)
_NET_UNREACH = ("network is unreachable", "net unreachable", "síť je nedostupná", "cílová síť není dostupná",
                "zielnetz nicht erreichbar", "destination net unreachable")
_HOST_UNREACH = ("host unreachable", "cílový hostitel není dostupný", "zielhost nicht erreichbar",
                 "no route to host", "destination host unreachable")


def parse_ping_output(text: str, returncode: int, platform: str = sys.platform) -> tuple[PingStatus, float | None]:
    """Parsování výstupu systémového ping (odolné vůči lokalizaci).

    Úspěch = nalezen čas odpovědi *a* TTL (Windows vrací kód 0 i u
    "Destination host unreachable", proto se na návratový kód nespoléháme).
    """
    low = text.lower()
    m = _RTT_RE.search(text)
    has_ttl = bool(_TTL_RE.search(text))
    if m and (has_ttl or (returncode == 0 and platform != "win32")):
        return PingStatus.OK, float(m.group(1).replace(",", "."))
    if any(k in low for k in _NET_UNREACH):
        return PingStatus.NET_UNREACHABLE, None
    if any(k in low for k in _HOST_UNREACH):
        return PingStatus.HOST_UNREACHABLE, None
    if "unknown host" in low or "could not find host" in low or "name or service not known" in low:
        return PingStatus.DNS_ERROR, None
    return PingStatus.TIMEOUT, None


class SystemPinger(Pinger):
    name = "system-ping"

    def __init__(self, executable: str | None = None):
        import shutil
        self.exe = executable or shutil.which("ping") or "ping"

    def build_args(self, address: str, timeout: float, platform: str = sys.platform) -> list[str]:
        v6 = ":" in address
        if platform == "win32":
            return [self.exe, "-n", "1", "-w", str(max(1, int(timeout * 1000))), "-6" if v6 else "-4", address]
        if platform == "darwin":
            exe = "/sbin/ping6" if v6 and os.path.exists("/sbin/ping6") else self.exe
            args = [exe, "-n", "-c", "1"]
            if not exe.endswith("ping6"):
                args += ["-W", str(max(1, int(timeout * 1000)))]   # macOS: ms
            return args + [address]
        wait = max(1, int(timeout + 0.999))                       # iputils/busybox: celé sekundy
        return [self.exe, "-n", "-c", "1", "-W", str(wait)] + (["-6"] if v6 else []) + [address]

    def ping(self, address: str, timeout: float) -> PingResult:
        args = self.build_args(address, timeout)
        env = dict(os.environ, LC_ALL="C", LANG="C")
        kwargs: dict = dict(capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout + 3, env=env)
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            t0 = time.perf_counter()
            proc = subprocess.run(args, check=False, **kwargs)   # bez shell=True
            wall = (time.perf_counter() - t0) * 1000
        except subprocess.TimeoutExpired:   # run() proces sám ukončí a uklidí
            return PingResult(PingStatus.TIMEOUT, address=address)
        except OSError as e:
            return PingResult(PingStatus.ERROR, address=address, error=f"ping: {e}")
        enc = "oem" if sys.platform == "win32" else "utf-8"
        try:
            out = (proc.stdout + proc.stderr).decode(enc, errors="replace")
        except LookupError:
            out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        st, rtt = parse_ping_output(out, proc.returncode)
        if st is PingStatus.OK and rtt is not None and rtt > timeout * 1000:
            return PingResult(PingStatus.TIMEOUT, address=address, error=f"pozdní odpověď {rtt:.0f} ms")
        del wall
        return PingResult(st, rtt_ms=rtt if st.is_ok else None, address=address)


# ---------------------------------------------------------------------------

def select_pinger(prefer: str = "auto") -> Pinger:
    if prefer == "system":
        return SystemPinger()
    if sys.platform == "win32" and prefer in ("auto", "native"):
        try:
            return WindowsIcmpPinger()
        except (OSError, AttributeError) as e:
            log.warning("IcmpSendEcho nedostupné (%s) – použiji systémový ping", e)
            return SystemPinger()
    if prefer in ("auto", "native"):
        p = SocketIcmpPinger.probe()
        if p:
            return p
        log.info("ICMP socket bez oprávnění nedostupný – použiji systémový ping "
                 "(tip: sudo sysctl net.ipv4.ping_group_range='0 2147483647')")
    return SystemPinger()


def ping_host(host: str, timeout: float, pinger: Pinger, resolver: Resolver) -> PingResult:
    """Přeloží doménu a provede jeden ping. Nikdy nevyhazuje výjimku."""
    try:
        ip, err = resolver.resolve(host)
        if not ip:
            return PingResult(PingStatus.DNS_ERROR, error=err)
        return pinger.ping(ip, timeout)
    except Exception as e:  # noqa: BLE001 – chyba jedné sondy nesmí shodit engine
        log.exception("Neočekávaná chyba pingu %s", host)
        return PingResult(PingStatus.ERROR, error=str(e))
