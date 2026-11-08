"""Regrese L11: samostatná zařízení WoL, bez skutečných paketů a dat ze sítě."""
import threading
import time
from typing import Any
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

from netmonitor.core import wol
from netmonitor.core.config import AppConfig, WolDevice, load_config, save_config
from netmonitor.core.network_info import InterfaceInfo, NetworkSnapshot
from netmonitor.gui import controller as cmod, tools_dialog as tools, wol_dialog as gui
from netmonitor.gui.main_widget import MainWidget

from .fakes import FakePinger

MAC = "02:00:00:00:00:01"
HOST = "192.0.2.10"
SEND = wol.send  # skutečná funkce se v testu portu spustí výhradně s falešným socketem


def pump_until(predicate):
    end = time.monotonic() + 4
    while not predicate() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert predicate()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ctrl(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(cmod, "select_pinger", lambda: FakePinger())
    cfg = AppConfig.from_dict({"ping": {"targets": []}, "network": {"external_api": False}})
    c = cmod.Controller(cfg, tmp_path / "config.json", tmp_path / "h.sqlite3")
    c.network = NetworkSnapshot(active=InterfaceInfo(name="test0", display_name="Test",
                               ipv4=("192.0.2.1",), ipv4_masks=("255.255.255.0",)))
    yield c
    c.shutdown()
    for d in list(tools._wol_dialogs):
        if d.ctrl is c:
            tools._wol_dialogs.remove(d)
            d.close()
            d.deleteLater()


@pytest.fixture(autouse=True)
def no_packets(monkeypatch):
    send = Mock(return_value=["192.0.2.255:9"])
    monkeypatch.setattr(wol, "send", send)
    return send


def test_old_config_keeps_ping_mac_and_broadcast(tmp_path):
    """Regrese: staré cíle se nemigrují do samostatného seznamu a zůstávají beze změny."""
    target = {"id": "old", "name": "PC", "host": HOST, "mac": MAC,
              "wol_broadcast": "192.0.2.255", "enabled": False}
    cfg = AppConfig.from_dict({"schema_version": 3, "ping": {"targets": [target]}})
    assert cfg.wol.devices == []
    for key, value in target.items():
        assert getattr(cfg.ping.targets[0], key) == value
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.to_dict() == cfg.to_dict()


def test_wol_config_roundtrip_defaults_and_duplicate_ids(tmp_path):
    cfg = AppConfig.from_dict({"ping": {"targets": []}, "wol": {"devices": [
        {"id": "same", "name": "PC", "mac": MAC},
        {"id": "same", "name": "Server", "mac": "02:00:00:00:00:02", "port": 40009,
         "broadcast": "192.0.2.255", "host": "example.com", "future": True}]}})
    assert len(cfg.wol.devices) == 2
    assert cfg.wol.devices[0].port == 9
    assert cfg.wol.devices[0].host == cfg.wol.devices[0].broadcast == ""
    assert cfg.wol.devices[0].id != cfg.wol.devices[1].id
    path = tmp_path / "config.json"
    save_config(cfg, path)
    assert load_config(path).to_dict() == cfg.to_dict()
    assert cfg.real_targets() == []


@pytest.mark.parametrize("field,value", [("mac", "bad"), ("mac", "00:00:00:00:00:00"),
    ("broadcast", "example.com"), ("broadcast", "2001:db8::1"), ("broadcast", "192.0.2.999"),
    ("port", 0), ("port", 65536), ("port", True), ("port", None), ("port", 9.5), ("port", "9"),
    ("host", "192.0.2.999"), ("name", "")])
def test_invalid_device_is_rejected(field, value):
    raw = {"name": "PC", "mac": MAC, field: value}
    assert WolDevice(**raw).validate()
    assert not AppConfig.from_dict({"wol": {"devices": [raw]}}).wol.devices


@pytest.mark.parametrize("port", [1, 9, 65535])
def test_valid_device_ports(port):
    assert not WolDevice(name="PC", mac=MAC, port=port, host=HOST, broadcast="192.0.2.255").validate()


@pytest.mark.parametrize("text", [
    f"192.0.2.10 dev test0 lladdr {MAC} REACHABLE\n192.0.2.11 dev test0 FAILED\n",
    f"IP address HW type Flags HW address Mask Device\n192.0.2.10 0x1 0x2 {MAC} * test0\n"
    "192.0.2.11 0x1 0x0 00:00:00:00:00:00 * test0",
    "Rozhraní: 192.0.2.1 --- 0x7\nInternetová adresa Fyzická adresa Typ\n"
    "192.0.2.10 02-00-00-00-00-01 dynamická\n192.0.2.255 ff-ff-ff-ff-ff-ff statická",
    "? (192.0.2.10) at 2:0:0:0:0:1 on en0 ifscope [ethernet]\n? (192.0.2.11) at (incomplete) on en0",
])
def test_neighbor_parsers(text):
    assert wol.parse_neighbors(text + "\n" + text) == [wol.Neighbor(HOST, MAC)]


def test_neighbors_skip_invalid_and_multicast():
    assert wol.parse_neighbors(f"192.0.2.999 dev test0 lladdr {MAC} STALE\n"
        "224.0.0.1 dev test0 lladdr 01:00:5e:00:00:01 STALE\n"
        f"2001:db8::192.0.2.10 dev test0 lladdr {MAC} STALE") == []


@pytest.mark.parametrize("platform,args", [("linux", ["ip", "neigh", "show"]),
    ("win32", ["arp", "-a"]), ("darwin", ["arp", "-an"])])
def test_neighbor_commands(monkeypatch, platform, args):
    monkeypatch.setattr(wol.sys, "platform", platform)
    run = Mock(return_value=f"192.0.2.10 lladdr {MAC}")
    monkeypatch.setattr(wol, "run_cmd", run)
    assert wol.list_neighbors() == [wol.Neighbor(HOST, MAC)]
    assert run.call_args.args == (args,)
    assert run.call_args.kwargs["timeout"] == 3


def test_linux_neighbors_fallback(monkeypatch):
    monkeypatch.setattr(wol.sys, "platform", "linux")
    monkeypatch.setattr(wol, "run_cmd", lambda *a, **kw: "")
    monkeypatch.setattr(wol.Path, "read_text", lambda *a: f"192.0.2.10 0x1 0x2 {MAC} * test0")
    assert wol.list_neighbors() == [wol.Neighbor(HOST, MAC)]


def test_dialog_crud_and_validation(ctrl, monkeypatch):
    dialog = gui.WolDevicesDialog(ctrl)
    dialog.show()
    assert dialog.broadcast.text() == "192.0.2.255" and dialog.port.value() == 9
    assert dialog.save() is None and dialog.error.text()
    dialog.name.setText("PC")
    dialog.mac.setText(MAC)
    device = dialog.save()
    assert device and dialog.table.topLevelItemCount() == 1
    assert not ctrl.cfg.real_targets()
    dialog.new()
    item = dialog.table.topLevelItem(0)
    assert item is not None
    dialog.table.setCurrentItem(item)
    assert dialog.mac.text() == MAC
    dialog.name.setText("Server")
    dialog.port.setValue(40009)
    dialog.host.setText(HOST)
    saved = dialog.save()
    assert saved and saved.id == device.id
    assert ctrl.cfg.wol.devices[0].name == "Server"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    dialog.remove()
    assert len(ctrl.cfg.wol.devices) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    dialog.remove()
    assert not ctrl.cfg.wol.devices and not dialog.edit_id
    dialog.close()


def test_neighbor_loading_is_background_and_selects(ctrl, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def neighbors():
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        assert release.wait(3)
        return [wol.Neighbor(HOST, MAC)]

    monkeypatch.setattr(wol, "list_neighbors", neighbors)
    dialog = gui.NeighborsDialog(ctrl)
    dialog.show()
    try:
        pump_until(entered.is_set)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        pump_until(lambda: bool(heartbeat))
        assert not dialog.choose.isEnabled()
    finally:
        release.set()
    pump_until(lambda: dialog.table.topLevelItemCount() == 1)
    item = dialog.table.topLevelItem(0)
    assert item is not None
    dialog.table.setCurrentItem(item)
    assert dialog.choose.isEnabled() and dialog.selected() == wol.Neighbor(HOST, MAC)
    dialog.close()


def test_network_selection_prefills_editor(ctrl, monkeypatch):
    monkeypatch.setattr(wol, "list_neighbors", lambda: [wol.Neighbor(HOST, MAC)])

    def select(dialog):
        pump_until(lambda: dialog.table.topLevelItemCount() == 1)
        item = dialog.table.topLevelItem(0)
        assert item is not None
        dialog.table.setCurrentItem(item)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(gui.NeighborsDialog, "exec", select)
    dialog = gui.WolDevicesDialog(ctrl)
    dialog._neighbors()
    assert dialog.mac.text() == MAC and dialog.host.text() == HOST
    assert dialog.broadcast.text() == "192.0.2.255"
    dialog.close()


@pytest.mark.parametrize("result", [[], OSError("syntetická chyba")])
def test_neighbor_empty_or_error_restores_ui(ctrl, monkeypatch, result):
    def neighbors():
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(wol, "list_neighbors", neighbors)
    dialog = gui.NeighborsDialog(ctrl)
    dialog.show()
    pump_until(lambda: not ctrl._busy)
    assert ("nelze" if isinstance(result, Exception) else "prázdná") in dialog.status.text()
    assert not dialog.choose.isEnabled()
    dialog.close()


@pytest.mark.parametrize("host", ["", HOST])
def test_standalone_wake_background_and_optional_checks(ctrl, monkeypatch, no_packets, host):
    thread = []
    no_packets.side_effect = lambda *a, **kw: thread.append(threading.current_thread()) or ["192.0.2.255:40009"]
    monkeypatch.setattr(tools, "WOL_CHECK_DELAYS_MS", (1, 2, 3))
    device = WolDevice(name="PC", mac=MAC, host=host, port=40009, broadcast="192.0.2.255")
    tools.wake(ctrl, device)
    dialog = tools._wol_dialogs[-1]
    pump_until(lambda: not dialog.busy())
    assert not ctrl.cfg.real_targets()
    assert thread and thread[0] is not threading.main_thread()
    no_packets.assert_called_once_with(MAC, "192.0.2.1", "255.255.255.0", "192.0.2.255", port=40009)
    assert ctrl.pinger.calls == ({HOST: 3} if host else {})
    assert ("Ověření" if host else "není nastaveno") in dialog.status.text()


def test_standalone_send_error_is_reported(ctrl, monkeypatch, no_packets):
    no_packets.side_effect = OSError("syntetická chyba")
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    tools.wake(ctrl, WolDevice(name="PC", mac=MAC, host=HOST))
    dialog = tools._wol_dialogs[-1]
    pump_until(lambda: not dialog.busy())
    assert "nezdařilo" in dialog.status.text()
    assert not ctrl.pinger.calls
    warning.assert_called_once()


@pytest.mark.parametrize("for_tray", [False, True])
def test_widget_and_tray_menus_offer_saved_devices(ctrl, monkeypatch, no_packets, for_tray):
    ctrl.cfg.wol.devices = [WolDevice(name="PC", mac=MAC, id="pc")]
    widget = MainWidget(ctrl)
    menu = widget.build_menu(for_tray=for_tray)
    action = next(a for a in menu.actions() if a.text() == "Probudit zařízení…")
    action.trigger()
    opened = widget._wol_devices_dialog
    assert opened is not None and opened.isVisible()
    quick = next(a.menu() for a in menu.actions() if a.text() == "Uložená zařízení WoL")
    assert isinstance(quick, QMenu)
    # Staré menu se při kliknutí řídí současnou konfigurací, nikoli zachyceným objektem.
    ctrl.cfg.wol.devices[0].mac = "02:00:00:00:00:02"
    quick.actions()[0].trigger()
    pump_until(lambda: no_packets.called)
    assert no_packets.call_args.args[0] == "02:00:00:00:00:02"
    opened.close()
    widget.close()


def test_explicit_udp_port_and_invalid_destination(monkeypatch):
    """Regrese: explicitní port nahrazuje 9/7 a neplatný vstup nesmí otevřít socket."""
    packets = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def sendto(self, packet, address):
            packets.append((packet, address))

    factory = Mock(return_value=Socket())
    monkeypatch.setattr(wol.socket, "socket", factory)
    assert SEND(MAC, extra_broadcast="192.0.2.255", port=40009) == ["255.255.255.255:40009", "192.0.2.255:40009"]
    assert packets == [(wol.magic_packet(wol.parse_mac(MAC)), (address, 40009))
                       for address in ("255.255.255.255", "192.0.2.255")]
    factory.reset_mock()
    invalid: list[dict[str, Any]] = [{"port": 0}, {"port": True}, {"extra_broadcast": "example.com"}]
    for kwargs in invalid:
        with pytest.raises(ValueError):
            SEND(MAC, **kwargs)
    factory.assert_not_called()
