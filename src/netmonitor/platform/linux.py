"""Linux: brána z /proc/net/route, DNS přes resolvectl / nmcli / resolv.conf, Wi-Fi přes nmcli / iw."""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import shlex
import socket
import struct
from pathlib import Path

from ..utils.proc import run_cmd

log = logging.getLogger(__name__)

SYS_NET = Path("/sys/class/net")
STUB_ADDRS = {"127.0.0.53", "127.0.0.54"}
VPN_PREFIXES = ("tun", "tap", "wg", "ppp", "tailscale", "zt", "nordlynx", "proton", "ipsec", "vti", "utun")


def read_routes(path: str = "/proc/net/route") -> list[dict]:
    routes = []
    try:
        with open(path) as f:
            next(f)
            for line in f:
                p = line.split()
                if len(p) < 8:
                    continue
                flags = int(p[3], 16)
                routes.append({
                    "iface": p[0],
                    "dest": socket.inet_ntoa(struct.pack("<L", int(p[1], 16))),
                    "gateway": socket.inet_ntoa(struct.pack("<L", int(p[2], 16))),
                    "flags": flags,
                    "metric": int(p[6]),
                    "mask": socket.inet_ntoa(struct.pack("<L", int(p[7], 16))),
                })
    except (OSError, StopIteration, ValueError) as e:
        log.debug("Nelze číst %s: %s", path, e)
    return routes


def default_gateway(iface: str | None = None, path: str = "/proc/net/route") -> tuple[str | None, str | None]:
    best = None
    for r in read_routes(path):
        if r["dest"] != "0.0.0.0" or r["mask"] != "0.0.0.0" or not (r["flags"] & 0x1):  # RTF_UP
            continue
        if iface and r["iface"] != iface:
            continue
        if best is None or r["metric"] < best["metric"]:
            best = r
    if not best:
        return None, None
    gw = best["gateway"] if best["flags"] & 0x2 else None   # RTF_GATEWAY (0.0.0.0 = point-to-point/VPN)
    return gw, best["iface"]


def interface_kind(name: str) -> str:
    base = SYS_NET / name
    if name == "lo":
        return "loopback"
    if (base / "wireless").exists() or (base / "phy80211").exists():
        return "wifi"
    if name.startswith(VPN_PREFIXES):
        return "vpn"
    try:
        if int((base / "type").read_text().strip()) in (65534, 512, 776, 778):  # none/ppp/ipip/gre
            return "vpn"
    except (OSError, ValueError):
        pass
    if (base / "device").exists():
        return "ethernet"
    return "other"


def _resolvectl_dns(iface: str) -> list[str] | None:
    out = run_cmd(["resolvectl", "dns", iface], timeout=2)
    if not out:
        return None
    m = re.search(r":\s*(.*)$", out.strip().splitlines()[0])
    if not m:
        return None
    return [a.split("%")[0].split("#")[0] for a in m.group(1).split() if _is_ip(a.split("%")[0].split("#")[0])]


def _nmcli_dns(iface: str) -> list[str] | None:
    out = run_cmd(["nmcli", "-t", "-f", "IP4.DNS,IP6.DNS", "device", "show", iface], timeout=2)
    if not out:
        return None
    res = []
    for line in out.splitlines():
        if ":" in line:
            val = line.split(":", 1)[1].replace("\\:", ":").strip()
            if _is_ip(val):
                res.append(val)
    return res


def parse_resolv_conf(text: str) -> list[str]:
    res = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 2 and p[0] == "nameserver" and _is_ip(p[1].split("%")[0]):
            res.append(p[1].split("%")[0])
    return res


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def dns_servers(iface: str | None) -> tuple[list[str], str]:
    """Vrátí (servery, poznámka). Lokální stub 127.0.0.53 nikdy nevydává za upstream."""
    if iface:
        for fn, src in ((_resolvectl_dns, "systemd-resolved"), (_nmcli_dns, "NetworkManager")):
            got = fn(iface)
            if got:
                return got, f"zdroj: {src} (rozhraní {iface})"
    stub_note = ""
    try:
        conf = parse_resolv_conf(Path("/etc/resolv.conf").read_text())
    except OSError:
        conf = []
    if conf and all(a in STUB_ADDRS for a in conf):
        stub_note = f"systém používá lokální stub {conf[0]} (systemd-resolved)"
        try:
            up = parse_resolv_conf(Path("/run/systemd/resolve/resolv.conf").read_text())
            if up:
                return up, stub_note + "; zobrazeny upstream servery z /run/systemd/resolve/resolv.conf"
        except OSError:
            pass
        return conf, stub_note + " – upstream nezjištěn"
    return conf, "zdroj: /etc/resolv.conf" if conf else "DNS nezjištěno"


def _nm_unescape_split(line: str) -> list[str]:
    parts, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def wifi_info(iface: str) -> dict:
    info: dict = {"ssid": None, "signal_pct": None, "signal_dbm": None, "rate_mbps": None, "note": ""}
    out = run_cmd(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,RATE,DEVICE", "device", "wifi", "list", "ifname", iface,
                   "--rescan", "no"], timeout=3)
    if out:
        for line in out.splitlines():
            p = _nm_unescape_split(line)
            if len(p) >= 5 and p[0] == "yes":
                info["ssid"] = p[1] or None
                info["signal_pct"] = int(p[2]) if p[2].isdigit() else None
                m = re.search(r"(\d+(?:\.\d+)?)", p[3])
                info["rate_mbps"] = float(m.group(1)) if m else None
                break
    if info["ssid"] is None or info["rate_mbps"] is None:
        out = run_cmd(["iw", "dev", iface, "link"], timeout=2)
        if out:
            m = re.search(r"^\s*SSID:\s*(.+)$", out, re.M)
            if m and not info["ssid"]:
                info["ssid"] = m.group(1).strip()
            m = re.search(r"signal:\s*(-?\d+)\s*dBm", out)
            if m:
                info["signal_dbm"] = int(m.group(1))
            m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s", out)
            if m and info["rate_mbps"] is None:
                info["rate_mbps"] = float(m.group(1))
    if info["signal_pct"] is None:
        try:   # /proc/net/wireless: "wlan0: 0000   54.  -56.  -256 ..."
            for line in Path("/proc/net/wireless").read_text().splitlines()[2:]:
                if line.strip().startswith(iface + ":"):
                    q = float(line.split()[2].rstrip("."))
                    info["signal_pct"] = int(min(100, q * 100 / 70))
        except (OSError, ValueError, IndexError):
            pass
    return info


def link_speed_sysfs(iface: str) -> int | None:
    try:
        v = int((SYS_NET / iface / "speed").read_text().strip())
        return v if v > 0 else None
    except (OSError, ValueError):
        return None


def adapter_details() -> dict[str, dict]:
    """Na Linuxu doplňuje jen typ; brána/DNS se zjišťují cíleně pro aktivní rozhraní."""
    res = {}
    try:
        for p in SYS_NET.iterdir():
            res[p.name] = {"kind": interface_kind(p.name), "display_name": p.name}
    except OSError:
        pass
    return res


# --- autostart (XDG) -------------------------------------------------------

def _autostart_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "netmonitor.desktop"


def autostart_enabled() -> bool:
    return _autostart_file().exists()


def set_autostart(enabled: bool, command: list[str]) -> None:
    f = _autostart_file()
    if not enabled:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(shlex.quote(c) for c in command)
    f.write_text(
        "[Desktop Entry]\nType=Application\nName=NetMonitor\n"
        f"Exec={exec_line}\nIcon=netmonitor\nComment=Síťový monitor\n"
        "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\nTerminal=false\n",
        encoding="utf-8",
    )
