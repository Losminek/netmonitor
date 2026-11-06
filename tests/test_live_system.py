"""Integrační testy na skutečném systému (síťové údaje aktuálního OS)."""
import sys

import pytest

from netmonitor.core.network_info import NetworkInfoProvider


@pytest.mark.integration
def test_live_network_snapshot():
    snap = NetworkInfoProvider().snapshot()
    assert snap.error == ""
    if not snap.has_default_route:
        pytest.skip("Bez výchozí trasy (offline stroj)")
    a = snap.active
    assert a is not None and a.ipv4, snap
    assert a.kind in ("wifi", "ethernet", "vpn", "other")


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="jen Windows")
def test_windows_get_adapters_addresses():
    from netmonitor.platform import windows
    ad = windows.adapter_details()
    assert ad, "GetAdaptersAddresses nevrátilo žádný adaptér"
    up = [a for a in ad.values() if a["up"] and a["ips"]]
    assert up, ad
    gw, iface = windows.default_gateway()
    assert gw is None or "." in gw
    assert windows.autostart_enabled() in (True, False)
