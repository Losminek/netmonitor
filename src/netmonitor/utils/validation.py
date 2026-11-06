"""Validace adres a domén (bez síťových dotazů)."""
from __future__ import annotations

import ipaddress
import re

_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value.strip())
        return True
    except (ipaddress.AddressValueError, ValueError):
        return False


def is_ipv6(value: str) -> bool:
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    try:
        ipaddress.IPv6Address(v)
        return True
    except (ipaddress.AddressValueError, ValueError):
        return False


def is_domain(value: str) -> bool:
    """Hostname dle RFC 1123, podpora IDN (převod na punycode)."""
    v = value.strip().rstrip(".")
    if not v or len(v) > 253:
        return False
    if is_ipv4(v) or is_ipv6(v):
        return False
    try:
        v = v.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return False
    labels = v.split(".")
    if all(lbl.isdigit() for lbl in labels):  # "1.2.3" není doména
        return False
    return all(_LABEL.match(lbl) for lbl in labels)


def is_valid_host(value: str) -> bool:
    return is_ipv4(value) or is_ipv6(value) or is_domain(value)


def host_kind(value: str) -> str:
    """'ipv4' | 'ipv6' | 'domain' | 'invalid'"""
    if is_ipv4(value):
        return "ipv4"
    if is_ipv6(value):
        return "ipv6"
    if is_domain(value):
        return "domain"
    return "invalid"


def is_private_address(addr: str) -> bool:
    """Lokální/neveřejná adresa (RFC1918, link-local, CGNAT, loopback, ULA)."""
    try:
        ip = ipaddress.ip_address(addr.strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.IPv4Network("100.64.0.0/10"):
        return True
    return not ip.is_global
