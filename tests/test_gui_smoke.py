"""Integrační test GUI (offscreen): widget, nastavení, historie, živé změny konfigurace, bezpečné ukončení."""
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from netmonitor.core.config import AppConfig, PingTarget  # noqa: E402

from .fakes import FakePinger  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setProperty("tray_available", False)
    yield app


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.02)


@pytest.fixture
def ctrl(qapp, tmp_path, monkeypatch):
    import netmonitor.gui.controller as cmod
    monkeypatch.setattr(cmod, "select_pinger", lambda: FakePinger({"10.0.0.99": "timeout"}, default="ok:7.1"))
    cfg = AppConfig.from_dict({"network": {"external_api": False}})
    cfg.ping.targets = [PingTarget(name="Zelina", host="10.0.0.1", interval_s=1, id="z"),
                        PingTarget(name="Mrtvý", host="10.0.0.99", interval_s=1, timeout_s=0.2, retries=2, id="d")]
    c = cmod.Controller(cfg, tmp_path / "config.json", tmp_path / "h.sqlite3")
    yield c
    c.shutdown()


def test_widget_shows_real_measurements(ctrl):
    from netmonitor.gui.main_widget import MainWidget
    w = MainWidget(ctrl)
    ctrl.start()
    w.show()
    pump(3.0)
    rows = w.ping.rows
    assert rows["z"]._value == ("7.1 ms", "ok")
    assert rows["d"]._value[0] == "TIMEOUT"
    assert ctrl.snapshots["d"].up is False
    assert w.net.wan.value.text() == "vypnuto"          # externí API vypnuto => žádný dotaz
    from PySide6.QtCore import Qt
    assert w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)     # skutečně průhledné okno
    w.close()


def test_live_config_change_and_save(ctrl, tmp_path):
    from netmonitor.gui.main_widget import MainWidget
    w = MainWidget(ctrl)
    ctrl.start()
    cfg = ctrl.cfg.copy()
    cfg.ping.targets.append(PingTarget(name="Nový", host="10.0.0.5", interval_s=1, id="n"))
    cfg.appearance.mode = "ontop"
    ctrl.update_config(cfg)
    pump(2.0)
    assert "n" in w.ping.rows
    assert ctrl.snapshots["n"].ok_count >= 1
    from PySide6.QtCore import Qt
    assert w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    from netmonitor.core.config import load_config
    saved = load_config(tmp_path / "config.json")
    assert [t.id for t in saved.ping.targets] == ["z", "d", "n"]


def test_dialogs_open(ctrl):
    from netmonitor.gui.history_dialog import HistoryDialog
    from netmonitor.gui.main_widget import MainWidget
    from netmonitor.gui.settings_dialog import SettingsDialog
    w = MainWidget(ctrl)
    ctrl.start()
    pump(1.5)
    s = SettingsDialog(ctrl, w)
    s.show()
    assert s.table.topLevelItemCount() == 2
    from PySide6.QtCore import Qt
    s.table.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
    pump(0.2)
    assert ctrl.cfg.target_by_id("d").enabled is False
    h = HistoryDialog(ctrl)
    h.show_target("z")
    pump(0.3)
    assert h.chart.points, "graf 1 h musí mít body z paměťového bufferu"
    menu = w.build_menu()
    assert any("Nastavení" in a.text() for a in menu.actions())
    h.close()
    s.close()


def test_shutdown_is_clean(ctrl):
    import threading
    ctrl.start()
    pump(1.0)
    ctrl.shutdown()
    time.sleep(0.2)
    assert not ctrl.engine.running
    alive = [t.name for t in threading.enumerate() if t.name.startswith(("ping", "bg"))]
    assert alive == []


def test_sections_in_widget_and_settings(ctrl):
    from netmonitor.core.config import new_section
    from netmonitor.gui.main_widget import MainWidget
    from netmonitor.gui.settings_dialog import SettingsDialog
    w = MainWidget(ctrl)
    ctrl.start()
    cfg = ctrl.cfg.copy()
    cfg.ping.targets.insert(0, new_section("Domény"))
    cfg.ping.targets.insert(2, new_section("Zařízení"))
    ctrl.update_config(cfg)
    pump(1.5)
    assert len(w.ping.sections) == 2 and len(w.ping.rows) == 2
    assert ctrl.snapshots["z"].ok_count >= 1
    s = SettingsDialog(ctrl, w)
    s.show()
    t = s.table
    assert t.topLevelItemCount() == 2 and t.topLevelItem(0).childCount() == 1
    assert t.flat_ids() == [x.id for x in ctrl.cfg.ping.targets]
    # přetažení (simulace výsledku dropu): sekce Zařízení nahoru
    sec2 = ctrl.cfg.ping.targets[2].id
    s._order_from_tree([sec2, "d", ctrl.cfg.ping.targets[0].id, "z"])
    pump(0.3)
    assert [x.id for x in ctrl.cfg.ping.targets][:2] == [sec2, "d"]
    assert list(w.ping._order)[:2] == [sec2, "d"]
    # šipka: sekce dolů
    t.select_id = sec2
    s._fill_table()
    s._move(1)
    assert [x.id for x in ctrl.cfg.ping.targets][-2:] == [sec2, "d"]
    s.close()


def test_ipconfig_dialog_prefill_and_validation(ctrl, monkeypatch):
    from netmonitor.core.network_info import InterfaceInfo, NetworkSnapshot
    from netmonitor.gui.ipconfig_dialog import IpConfigDialog
    import netmonitor.platform as plat
    a = InterfaceInfo(name="Ethernet 10Gb", display_name="Ethernet 10Gb", kind="ethernet", is_up=True,
                      ipv4=("192.168.14.112",), ipv4_masks=("255.255.255.0",), gateway="192.168.14.1",
                      dns=("192.168.14.2", "192.168.14.3"))
    ctrl.network = NetworkSnapshot(active=a, interfaces=(a,), has_default_route=True, default_iface=a.name)

    class FakePlat:
        applied = []

        @staticmethod
        def dhcp_enabled(_i):
            return True

        @staticmethod
        def apply_ip(s):
            FakePlat.applied.append(s)
            return True, "ok"
    monkeypatch.setattr(plat, "current", lambda: FakePlat)
    d = IpConfigDialog(ctrl)
    d.open_for("Ethernet 10Gb")
    assert d.rb_dhcp.isChecked() and d.ip.text() == "192.168.14.112" and d.mask.text() == "255.255.255.0"
    assert "DHCP" in d.current.text()
    d.rb_static.setChecked(True)
    d.rb_dns_static.setChecked(True)
    d.ip.setText("192.168.14.300")
    assert not d.btn_apply.isEnabled()
    d.ip.setText("192.168.14.112")
    assert d.btn_apply.isEnabled()
    s = d.settings()
    assert (s.dhcp, s.ip, s.prefix, s.gateway, s.dns_list) == (False, "192.168.14.112", 24, "192.168.14.1",
                                                              ["192.168.14.2", "192.168.14.3"])
    results = []
    ctrl.apply_ip(s, results.append)
    pump(0.5)
    assert results == [(True, "ok")] and FakePlat.applied[0].ip == "192.168.14.112"
    # profil
    monkeypatch.setattr("netmonitor.gui.ipconfig_dialog.QInputDialog.getText", lambda *a, **k: ("Kancelář", True))
    d._save_profile()
    assert ctrl.cfg.network.ip_profiles[0]["name"] == "Kancelář"
    d.close()
