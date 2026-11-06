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
    assert s.table.rowCount() == 2
    from PySide6.QtCore import Qt
    s.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
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
