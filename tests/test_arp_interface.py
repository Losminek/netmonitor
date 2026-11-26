"""Regrese L51: ARP brány patří aktivnímu rozhraní, bez čtení skutečné sítě."""
from unittest.mock import Mock

import pytest

from netmonitor.core import wol
from netmonitor.core.network_identity import identify
from netmonitor.core.network_info import InterfaceInfo, NetworkSnapshot

IP = "192.0.2.1"
ETH_MAC = "02:00:00:00:00:01"
WIFI_MAC = "02:00:00:00:00:02"
PROC_ARP = f"""IP address       HW type     Flags       HW address            Mask     Device
{IP} 0x1 0x2 {ETH_MAC} * eth0
{IP} 0x1 0x2 {WIFI_MAC} * wlan0
"""


@pytest.mark.parametrize("iface,expected", [("wlan0", WIFI_MAC), ("eth0", ETH_MAC),
                                           ("wlan", None), ("eth1", None)])
def test_linux_arp_lookup_filters_device(monkeypatch, iface, expected):
    monkeypatch.setattr(wol.sys, "platform", "linux")
    monkeypatch.setattr(wol.Path, "read_text", Mock(return_value=PROC_ARP))
    monkeypatch.setattr(wol, "run_cmd", Mock(return_value=""))
    assert wol.arp_lookup(IP, iface=iface) == expected


def test_linux_arp_lookup_without_interface_keeps_first_match(monkeypatch):
    monkeypatch.setattr(wol.sys, "platform", "linux")
    read = Mock(return_value=PROC_ARP)
    run = Mock()
    monkeypatch.setattr(wol.Path, "read_text", read)
    monkeypatch.setattr(wol, "run_cmd", run)
    assert wol.arp_lookup(IP) == ETH_MAC
    read.assert_called_once_with()
    run.assert_not_called()


@pytest.mark.parametrize("mask,device", [(IP, "eth0"), ("*", IP)])
def test_proc_arp_matches_only_ip_column(mask, device):
    text = f"198.51.100.1 0x1 0x2 {ETH_MAC} {mask} {device}\n"
    assert wol.parse_arp_output(text, IP) is None
    assert wol.parse_arp_output(text + PROC_ARP, IP) == ETH_MAC


@pytest.mark.parametrize("iface", [None, "wlan0"])
def test_linux_arp_fallback_command(monkeypatch, iface):
    monkeypatch.setattr(wol.sys, "platform", "linux")
    monkeypatch.setattr(wol.Path, "read_text", Mock(side_effect=OSError("syntetická chyba")))
    run = Mock(return_value=f"{IP} dev wlan0 lladdr {WIFI_MAC} REACHABLE\n")
    monkeypatch.setattr(wol, "run_cmd", run)
    assert wol.arp_lookup(IP, iface) == WIFI_MAC
    args = ["ip", "neigh", "show", IP] + (["dev", iface] if iface else [])
    run.assert_called_once_with(args, timeout=3)


@pytest.mark.parametrize("platform,args,kwargs,output", [
    ("darwin", ["arp", "-n", IP], {"timeout": 3},
     f"? ({IP}) at {ETH_MAC} on en0 ifscope [ethernet]"),
    ("win32", ["arp", "-a", IP], {"timeout": 3, "c_locale": False},
     f"{IP} 02-00-00-00-00-01 dynamická"),
])
def test_other_platforms_ignore_interface(monkeypatch, platform, args, kwargs, output):
    monkeypatch.setattr(wol.sys, "platform", platform)
    run = Mock(return_value=output)
    monkeypatch.setattr(wol, "run_cmd", run)
    assert wol.arp_lookup(IP, iface="wlan0") == ETH_MAC
    run.assert_called_once_with(args, **kwargs)


@pytest.mark.parametrize("iface", ["eth0", "wlan0"])
def test_identify_passes_active_interface(iface):
    active = InterfaceInfo(name=iface, display_name=iface, gateway=IP)
    arp = Mock(return_value=WIFI_MAC)
    result = identify(NetworkSnapshot(active=active), arp=arp)
    arp.assert_called_once_with(IP, iface)
    assert result == (f"lan:{IP}|{WIFI_MAC}", f"síť s bránou {IP}")
