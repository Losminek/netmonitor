"""Wake-on-LAN (magic packet) a zjištění MAC adresy z ARP tabulky systému.

Magic packet = 6× 0xFF + 16× MAC adresa, posláno UDP broadcastem (porty 9 a 7).
Posílá se na 255.255.255.255 i na broadcast podsítě aktivní karty a socket se váže na IP
této karty – na počítači s více síťovkami tak paket odejde správným rozhraním.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import sys
from pathlib import Path

from ..utils.proc import run_cmd

_MAC_ANY = re.compile(r"^[0-9a-fA-F]{12}$")
_MAC_IN_TEXT = re.compile(r"(?<![0-9a-fA-F:-])((?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2})(?![0-9a-fA-F:-])")
WOL_PORTS = (9, 7)


def parse_mac(text: str) -> bytes:
    """Přijímá aa:bb:cc:dd:ee:ff, AA-BB-…, aabb.ccdd.eeff i aabbccddeeff. Neplatná => ValueError."""
    s = str(text).strip()
    parts = re.split(r"[:-]", s)
    if len(parts) == 6 and all(1 <= len(p) <= 2 for p in parts):
        s = "".join(p.zfill(2) for p in parts)
    else:
        s = s.replace(".", "")
    if not _MAC_ANY.match(s):
        raise ValueError(f"Neplatná MAC adresa: {text!r}")
    mac = bytes.fromhex(s)
    if mac == b"\x00" * 6 or mac == b"\xff" * 6:
        raise ValueError("MAC adresa nesmí být samé nuly ani broadcast")
    return mac


def format_mac(text_or_bytes) -> str:
    mac = text_or_bytes if isinstance(text_or_bytes, bytes) else parse_mac(text_or_bytes)
    return ":".join(f"{b:02X}" for b in mac)


def magic_packet(mac: bytes) -> bytes:
    return b"\xff" * 6 + mac * 16


def directed_broadcast(ip: str, mask: str) -> str | None:
    try:
        net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    except ValueError:
        return None
    return str(net.broadcast_address) if net.prefixlen < 31 else None


def send(mac_text: str, local_ip: str | None = None, netmask: str | None = None,
         extra_broadcast: str | None = None) -> list[str]:
    """Odešle magic packet. Vrací seznam cílů (adresa:port), kam odešel. Chyba => OSError/ValueError."""
    pkt = magic_packet(parse_mac(mac_text))
    targets: list[str] = ["255.255.255.255"]
    if local_ip and netmask:
        b = directed_broadcast(local_ip, netmask)
        if b and b not in targets:
            targets.append(b)
    if extra_broadcast:
        ipaddress.IPv4Address(extra_broadcast)        # validace
        if extra_broadcast not in targets:
            targets.append(extra_broadcast)
    sent = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if local_ip:
            try:
                s.bind((local_ip, 0))
            except OSError:
                pass
        for addr in targets:
            for port in WOL_PORTS:
                s.sendto(pkt, (addr, port))
                sent.append(f"{addr}:{port}")
    return sent


# ---------------------------------------------------------------------------- ARP

def parse_arp_output(text: str, ip: str) -> str | None:
    """Najde MAC u dané IP ve výstupu `arp -a` (Windows, libovolný jazyk) / `arp -n` (macOS) / /proc/net/arp."""
    for line in text.splitlines():
        if not re.search(rf"(?<![\d.]){re.escape(ip)}(?![\d.])", line):
            continue
        m = _MAC_IN_TEXT.search(line)
        if m:
            try:
                mac = parse_mac(m.group(1))
            except ValueError:
                continue
            return format_mac(mac)
    return None


def arp_lookup(ip: str) -> str | None:
    """MAC adresa IPv4 z ARP tabulky (zařízení musí být nedávno dostupné – nejdřív ho pingněte)."""
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        return None
    if sys.platform.startswith("linux"):
        try:
            return parse_arp_output(Path("/proc/net/arp").read_text(), ip)
        except OSError:
            pass
        out = run_cmd(["ip", "neigh", "show", ip], timeout=3) or ""
        return parse_arp_output(out, ip)
    if sys.platform == "darwin":
        return parse_arp_output(run_cmd(["arp", "-n", ip], timeout=3) or "", ip)
    return parse_arp_output(run_cmd(["arp", "-a", ip], timeout=3, c_locale=False) or "", ip)
