"""Zjištění aktivního síťového rozhraní, LAN IP, brány, rychlosti linky, Wi-Fi a DNS.

Aktivní rozhraní = rozhraní, přes které vede výchozí trasa. Zjišťuje se
spolehlivě a bez odeslání paketu: UDP socket ``connect()`` na veřejnou adresu
nastaví zdrojovou IP podle směrovací tabulky; tu pak dohledáme v psutil.

Pozor: "Speed" je *linková* rychlost adaptéru (vyjednaná s AP/switchem),
nikoli rychlost internetu. V UI je tak i popsána.
"""
from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field

import psutil

from .. import platform as plat

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterfaceInfo:
    name: str
    display_name: str
    kind: str = "other"               # wifi | ethernet | vpn | loopback | other
    is_up: bool = False
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()
    mac: str = ""
    speed_mbps: float | None = None   # linková rychlost
    ssid: str | None = None
    signal_pct: int | None = None
    signal_dbm: int | None = None
    wifi_note: str = ""
    gateway: str | None = None
    dns: tuple[str, ...] = ()
    dns_note: str = ""

    @property
    def kind_label(self) -> str:
        return {"wifi": "Wireless", "ethernet": "Ethernet", "vpn": "VPN", "loopback": "Loopback"}.get(
            self.kind, "Adapter")


@dataclass(frozen=True)
class NetworkSnapshot:
    active: InterfaceInfo | None
    interfaces: tuple[InterfaceInfo, ...] = ()
    has_default_route: bool = False
    default_iface: str | None = None
    ts: float = field(default_factory=time.time)
    error: str = ""

    def signature(self) -> tuple:
        a = self.active
        if not a:
            return (None, self.has_default_route)
        return (a.name, a.ipv4, a.gateway, a.dns, a.ssid, self.has_default_route)


def route_source_ip(family: int = socket.AF_INET) -> str | None:
    probe = ("192.0.2.1", 9) if family == socket.AF_INET else ("2001:db8::1", 9, 0, 0)
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect(probe)   # UDP connect neposílá žádná data
            return s.getsockname()[0]
    except OSError:
        return None


def _psutil_interfaces() -> dict[str, dict]:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    res = {}
    for name, lst in addrs.items():
        v4 = tuple(a.address for a in lst if a.family == socket.AF_INET)
        v6 = tuple(a.address.split("%")[0] for a in lst if a.family == socket.AF_INET6)
        mac = next((a.address for a in lst if a.family == psutil.AF_LINK), "")
        st = stats.get(name)
        res[name] = {"ipv4": v4, "ipv6": v6, "mac": mac, "is_up": bool(st and st.isup),
                     "speed": (st.speed if st and st.speed and st.speed > 0 else None)}
    return res


class NetworkInfoProvider:
    def __init__(self, platform_mod=None):
        self.p = platform_mod or plat.current()

    def list_interfaces(self) -> list[str]:
        return sorted(_psutil_interfaces())

    def snapshot(self, preferred_iface: str | None = None, with_wifi: bool = True) -> NetworkSnapshot:
        try:
            return self._snapshot(preferred_iface, with_wifi)
        except Exception as e:  # noqa: BLE001
            log.exception("Chyba při zjišťování sítě")
            return NetworkSnapshot(active=None, error=str(e))

    def _snapshot(self, preferred_iface: str | None, with_wifi: bool) -> NetworkSnapshot:
        ifs = _psutil_interfaces()
        details = self.p.adapter_details() or {}
        src = route_source_ip()
        default_iface = None
        if src:
            default_iface = next((n for n, d in ifs.items() if src in d["ipv4"]), None)
        gw_default, gw_iface = self.p.default_gateway()
        if not default_iface and gw_iface in ifs:
            default_iface = gw_iface
        has_route = bool(src) or bool(gw_default)

        name = preferred_iface if preferred_iface in ifs else default_iface
        infos = []
        for n, d in ifs.items():
            det = details.get(n, {})
            infos.append(InterfaceInfo(name=n, display_name=det.get("display_name", n),
                                       kind=det.get("kind", "other"), is_up=d["is_up"], ipv4=d["ipv4"],
                                       ipv6=d["ipv6"], mac=d["mac"], speed_mbps=d["speed"]))
        if not name:
            return NetworkSnapshot(active=None, interfaces=tuple(infos), has_default_route=has_route,
                                   default_iface=default_iface)

        d, det = ifs[name], details.get(name, {})
        kind = det.get("kind") or "other"
        if kind == "other" and hasattr(self.p, "interface_kind"):
            kind = self.p.interface_kind(name)
        gateway = None
        if det.get("gateways"):
            gateway = next((g for g in det["gateways"] if ":" not in g), det["gateways"][0])
        if gateway is None:
            gateway, _ = self.p.default_gateway(name)
        if "dns" in det:   # Windows: už máme z GetAdaptersAddresses
            dns = [x for x in det["dns"] if not x.lower().startswith("fec0:")]
            dns_note = "zdroj: GetAdaptersAddresses" if dns else "DNS nezjištěno"
        else:
            dns, dns_note = self.p.dns_servers(name)
        speed = d["speed"] or det.get("speed_mbps")
        if speed is None and hasattr(self.p, "link_speed_sysfs"):
            speed = self.p.link_speed_sysfs(name)
        wifi = {}
        if kind == "wifi" and with_wifi:
            wifi = self.p.wifi_info(name) or {}
            if not speed and wifi.get("rate_mbps"):
                speed = wifi["rate_mbps"]
        active = InterfaceInfo(
            name=name, display_name=det.get("display_name", name), kind=kind, is_up=d["is_up"],
            ipv4=d["ipv4"], ipv6=tuple(a for a in d["ipv6"] if not a.lower().startswith("fe80")) or d["ipv6"],
            mac=d["mac"], speed_mbps=speed, ssid=wifi.get("ssid"), signal_pct=wifi.get("signal_pct"),
            signal_dbm=wifi.get("signal_dbm"), wifi_note=wifi.get("note", ""), gateway=gateway,
            dns=tuple(dns), dns_note=dns_note,
        )
        return NetworkSnapshot(active=active, interfaces=tuple(infos), has_default_route=has_route,
                               default_iface=default_iface)
