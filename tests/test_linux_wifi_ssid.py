from unittest.mock import Mock

import pytest

from netmonitor.platform import linux


@pytest.mark.parametrize(("escaped", "ssid"), [
    (r"Kancel\xc3\xa1\xc5\x99 2", "Kancelář 2"),
    (r"\x20Kancelar 2", " Kancelar 2"),
    (r"Kancelar 2\x20", "Kancelar 2 "),
    (r"Kancelar\x5c2", r"Kancelar\2"),
    (r"Kancelar\x5cx20", r"Kancelar\x20"),
    (r"Kancelar\x09\x00", "Kancelar\t\x00"),
    ("Kancelar 2", "Kancelar 2"),
])
def test_wifi_ssid_iw_matches_nmcli(monkeypatch, escaped, ssid):
    """Regrese L50: záložní iw musí vracet stejný SSID jako nmcli."""
    nmcli_ssid = ssid.replace("\\", "\\\\").replace(":", r"\:")
    nmcli_output = f"yes:{nmcli_ssid}:72:270 Mbit/s:wlan0\n"
    iw_output = (
        "Connected to 02:00:00:00:00:01 (on wlan0)\n"
        f"\tSSID: {escaped}\n\tsignal: -56 dBm\n\ttx bitrate: 270.0 MBit/s\n"
    )
    command = ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,RATE,DEVICE", "device", "wifi", "list",
               "ifname", "wlan0", "--rescan", "no"]
    mock_run = Mock(return_value=nmcli_output)
    monkeypatch.setattr(linux, "run_cmd", mock_run)
    # Fallback signálu také nesmí záviset na skutečném systému.
    monkeypatch.setattr(linux.Path, "read_text", lambda self: "")
    nmcli_info = linux.wifi_info("wlan0")
    mock_run.assert_called_once_with(command, timeout=3)
    assert nmcli_info["ssid"] == ssid

    mock_run.reset_mock()
    mock_run.side_effect = [None, iw_output]
    iw_info = linux.wifi_info("wlan0")
    assert mock_run.call_args_list[0].args[0] == command
    mock_run.assert_called_with(["iw", "dev", "wlan0", "link"], timeout=2)
    assert mock_run.call_count == 2
    assert iw_info["ssid"] == nmcli_info["ssid"]
    assert iw_info["signal_dbm"] == -56
    assert iw_info["rate_mbps"] == 270.0


@pytest.mark.parametrize(("escaped", "expected"), [
    (r"Kancelar\xff2", "Kancelar\ufffd2"),
    (r"Kancelar\xc3", "Kancelar\ufffd"),
])
def test_wifi_ssid_iw_invalid_utf8(monkeypatch, escaped, expected):
    """Regrese L50: neplatné UTF-8 se nahradí bez výjimky."""
    monkeypatch.setattr(linux, "run_cmd", lambda args, **kwargs:
                        f"\tSSID: {escaped}\n" if args[0] == "iw" else None)
    monkeypatch.setattr(linux.Path, "read_text", lambda self: "")
    assert linux.wifi_info("wlan0")["ssid"] == expected


@pytest.mark.parametrize("suffix", ["", " "])
@pytest.mark.parametrize("nmcli_ssid", [None, "Kancelar 2"])
def test_wifi_ssid_iw_empty(monkeypatch, suffix, nmcli_ssid):
    """Regrese L52: prázdné SSID nesmí převzít další řádek ani přepsat nmcli."""
    iw_output = (
        "Connected to 02:00:00:00:00:01 (on wlan0)\n"
        f"\tSSID:{suffix}\n\tfreq: 2412.0\n"
        "\tsignal: -56 dBm\n\ttx bitrate: 270.0 MBit/s\n"
    )
    nmcli_output = f"yes:{nmcli_ssid}:72::wlan0\n" if nmcli_ssid else None
    mock_run = Mock(side_effect=[nmcli_output, iw_output])
    monkeypatch.setattr(linux, "run_cmd", mock_run)
    monkeypatch.setattr(linux.Path, "read_text", lambda self: "")

    info = linux.wifi_info("wlan0")

    mock_run.assert_called_with(["iw", "dev", "wlan0", "link"], timeout=2)
    assert info["ssid"] == nmcli_ssid
    assert info["signal_dbm"] == -56
    assert info["rate_mbps"] == 270.0
