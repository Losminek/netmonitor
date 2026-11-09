"""Regrese B07/L18: bez tray musí widget zůstat ovladatelný a upozornění viditelná."""
import logging
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from netmonitor.core.config import AppConfig, export_config
from netmonitor.core.notifier import Notification
from netmonitor.gui.controller import Controller
from netmonitor.gui.main_widget import MainWidget
from netmonitor.gui.settings_dialog import SettingsDialog
from netmonitor.gui.tray import Tray

from .fakes import FakePinger


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.property("tray_available")
    app.setProperty("tray_available", False)
    yield app
    app.setProperty("tray_available", previous)


@pytest.fixture
def cfg():
    return AppConfig.from_dict({"network": {"external_api": False}, "ping": {"targets": []}})


@pytest.fixture
def controller_factory(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("netmonitor.gui.controller.select_pinger", FakePinger)
    monkeypatch.setattr("netmonitor.gui.controller.NetworkInfoProvider.list_interfaces", lambda _self: [])
    controllers = []

    def create(cfg):
        c = Controller(cfg, tmp_path / "config.json", tmp_path / f"h{len(controllers)}.sqlite3")
        controllers.append(c)
        return c

    yield create
    for c in controllers:
        c.shutdown()


def test_menu_and_toggle_cannot_hide_without_tray(cfg, controller_factory):
    """Regrese B07: menu ani přímé přepnutí nesmí ztratit jediný widget."""
    w = MainWidget(controller_factory(cfg))
    w.show()
    try:
        for for_tray in (False, True):
            menu = w.build_menu(for_tray=for_tray)
            assert not any("Skrýt do oznamovací oblasti" in a.text() and a.isEnabled()
                           for a in menu.actions())
            ct = next(a for a in menu.actions() if "click-through" in a.text())
            assert not ct.isEnabled()
        w.toggle_visible()
        assert w.isVisible()
    finally:
        w.close()


@pytest.mark.parametrize("route", ["initial", "update", "menu", "settings", "import"])
@pytest.mark.parametrize("tray_available", [False, True])
def test_click_through_all_config_paths(qapp, cfg, controller_factory, tmp_path, monkeypatch,
                                       route, tray_available):
    """Regrese B07: import i obecný update_config procházejí stejnou ochranou jako start."""
    qapp.setProperty("tray_available", tray_available)
    cfg.appearance.click_through = route == "initial"
    c = controller_factory(cfg)
    w = MainWidget(c)
    dialog = None
    try:
        if route == "update":
            updated = c.cfg.copy()
            updated.appearance.click_through = True
            c.update_config(updated, save=False)
        elif route == "menu":
            w._set_click_through(True)
        elif route in ("settings", "import"):
            dialog = SettingsDialog(c, w)
            if route == "settings":
                cb = next(cb for cb, section, key in dialog._binds
                          if section == "appearance" and key == "click_through")
                assert cb.isEnabled() is tray_available
                # I programové zapnutí zakázaného checkboxu musí projít ochranou.
                cb.setChecked(True)
            else:
                imported = c.cfg.copy()
                imported.appearance.click_through = True
                path = tmp_path / "import.json"
                export_config(imported, path)
                monkeypatch.setattr("netmonitor.gui.settings_dialog.QFileDialog.getOpenFileName",
                                    lambda *a, **kw: (str(path), "JSON (*.json)"))
                dialog._import()
        assert c.cfg.appearance.click_through is tray_available
        assert bool(w.windowFlags() & Qt.WindowType.WindowTransparentForInput) is tray_available
    finally:
        if dialog is not None:
            dialog.close()
        w.close()


@pytest.mark.parametrize("source", ["cli", "config"])
@pytest.mark.parametrize("tray_available", [False, True])
def test_minimized_start(qapp, cfg, controller_factory, monkeypatch, caplog, source, tray_available):
    """Regrese B07: skutečná startovací cesta bez tray ukáže widget a zapíše varování."""
    from netmonitor import main as entry

    cfg.general.start_minimized = source == "config"
    qapp.setProperty("tray_available", tray_available)
    c = controller_factory(cfg)
    monkeypatch.setattr(entry.cfgmod, "load_config", lambda _path: cfg)
    monkeypatch.setattr(entry.cfgmod, "save_config", lambda *a: None)
    monkeypatch.setattr(entry, "setup_logging", lambda *_a: None)
    monkeypatch.setattr(entry.signal, "signal", lambda *a: None)
    monkeypatch.setattr("netmonitor.gui.controller.Controller", lambda *a: c)
    monkeypatch.setattr(c, "start", lambda: None)
    monkeypatch.setattr("netmonitor.gui.updater_ui.UpdateManager", Mock())
    monkeypatch.setattr("netmonitor.platform.current", lambda: Mock(autostart_enabled=lambda: False))
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: tray_available)
    monkeypatch.setattr(QSystemTrayIcon, "show", lambda _self: None)
    # main normálně vytváří QApplication; v offscreen testu sdílí již existující instanci.
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", Mock(wraps=QApplication, return_value=qapp))
    widgets = []

    def create_widget(ctrl):
        w = MainWidget(ctrl)
        widgets.append(w)
        return w

    monkeypatch.setattr("netmonitor.gui.main_widget.MainWidget", create_widget)

    def check_visibility():
        assert widgets[0].isVisible() is not tray_available
        return 0

    monkeypatch.setattr(qapp, "exec", check_visibility)
    with caplog.at_level(logging.WARNING):
        try:
            assert entry.main(["--minimized"] if source == "cli" else []) == 0
            assert any("minimaliz" in r.message and "nedostupná" in r.message
                       for r in caplog.records) is not tray_available
        finally:
            qapp.aboutToQuit.disconnect(c.shutdown)
            for w in widgets:
                w.close()


@pytest.mark.parametrize("kind,critical", [("down", True), ("up", False), ("wol", False)])
def test_notification_without_tray(cfg, controller_factory, caplog, kind, critical):
    """Regrese L18: upozornění se bez tray zaznamená a dočasně zvýrazní ve widgetu."""
    c = controller_factory(cfg)
    w = MainWidget(c)
    w.show()
    n = Notification(kind, "test", "Test upozornění <test>", "Zařízení 192.0.2.1", critical)
    try:
        with caplog.at_level(logging.INFO):
            c.notification.emit(n)
        assert w.notification_notice.isVisible()
        assert w.notification_notice.textFormat() == Qt.TextFormat.PlainText
        assert n.title in w.notification_notice.text() and n.message in w.notification_notice.text()
        assert any(n.title in r.message and n.message in r.message for r in caplog.records)
        assert w._notification_timer.isActive()
        # Další zpráva obnoví jeden časovač, starší timeout ji neschová předčasně.
        c.notification.emit(Notification("up", "test", "Obnoveno", "192.0.2.1 odpovídá"))
        assert "Obnoveno" in w.notification_notice.text()
        w._notification_timer.timeout.emit()
        assert w.notification_notice.isHidden()
    finally:
        w.close()


def test_available_tray_keeps_visibility_and_notifications(qapp, cfg, controller_factory, monkeypatch):
    """Regrese L34: s dostupnou tray zůstane skrytí i doručování přes showMessage."""
    qapp.setProperty("tray_available", True)
    c = controller_factory(cfg)
    w = MainWidget(c)
    tray = Tray(w)
    show_message = Mock()
    monkeypatch.setattr(tray, "showMessage", show_message)
    try:
        w.show()
        menu = w.build_menu()
        hide = next(a for a in menu.actions() if "Skrýt do oznamovací oblasti" in a.text())
        hide.trigger()
        assert w.isHidden()
        w.toggle_visible()
        assert w.isVisible()
        n = Notification("down", "test", "Výpadek", "192.0.2.1 neodpovídá", True)
        c.notification.emit(n)
        show_message.assert_called_once_with(n.title, n.message, QSystemTrayIcon.MessageIcon.Warning, 8000)
        assert w.notification_notice.isHidden()
    finally:
        tray.deleteLater()
        w.close()


@pytest.mark.parametrize("success", [False, True])
def test_wol_result_without_tray(cfg, controller_factory, monkeypatch, success):
    """Regrese L18: současná WoL cesta doručí výsledek dialogem i bez tray ikony."""
    from netmonitor.core.config import PingTarget
    from netmonitor.gui import tools_dialog as tools

    c = controller_factory(cfg)
    target = PingTarget(name="Test", host="192.0.2.1", mac="02:00:00:00:00:01")
    send = Mock(return_value=["192.0.2.255:9"]) if success else Mock(side_effect=OSError("Test chyby"))
    monkeypatch.setattr(tools.wol, "send", send)
    info, warning = Mock(), Mock()
    monkeypatch.setattr(tools.QMessageBox, "information", info)
    monkeypatch.setattr(tools.QMessageBox, "warning", warning)
    # V testu se nespouštějí pozdější sondy ani skutečné odeslání WoL.
    monkeypatch.setattr(tools.QTimer, "singleShot", Mock())
    tools.wake(c, target)
    if success:
        info.assert_called_once()
        assert "Probouzecí paket odeslán" in info.call_args.args[2]
        warning.assert_not_called()
    else:
        warning.assert_called_once()
        assert "Test chyby" in warning.call_args.args[2]
        info.assert_not_called()
