"""Profily a účet v GUI: přepínání, automatická synchronizace, registrace klíčem, správa (offscreen)."""
import sys

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from netmonitor.core.config import AppConfig, PingTarget, load_config
from netmonitor.core.profiles import ProfileStore
from netmonitor.core.sync_client import SyncClient
from netmonitor.gui import account_tabs
from netmonitor.gui.settings_dialog import SettingsDialog
from netmonitor.gui.sync_service import SyncService

from .sync_server_fake import PASSWORD, URL, server  # noqa: F401
from .test_gui_smoke import ctrl, pump, qapp  # noqa: F401


@pytest.fixture
def env(ctrl, tmp_path, monkeypatch):
    """Controller s profily; síťové úlohy běží synchronně, aby byl test deterministický."""
    monkeypatch.setenv("NETMONITOR_SYNC_URL", URL)
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(ctrl.cfg)
    ctrl.profile_store = store
    monkeypatch.setattr(ctrl, "_run_bg", lambda key, fn, done=None: done(fn()) if done else fn())
    ctrl.sync = SyncService(ctrl)
    return ctrl, store


def dialog(ctrl):
    from netmonitor.gui.main_widget import MainWidget
    return SettingsDialog(ctrl, MainWidget(ctrl))


def select(tab, pid):
    for row in range(tab.table.rowCount()):
        if tab.table.item(row, 0).data(256) == pid:
            tab.table.setCurrentCell(row, 0)
            return
    raise AssertionError("profil není v tabulce")


def login(ctrl, email="user@example.org", code=None):
    result = []
    ctrl.sync.login(email, PASSWORD, code, True, result.append)
    assert result == [None], result
    return ctrl.sync


def test_create_edit_switch_delete_preserves_dns_and_restart(env, monkeypatch):
    ctrl, store = env
    d = dialog(ctrl)
    tab = d.profiles_tab
    home = store.active_id()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Pracovní", True))
    tab._create()
    work = store.active_id()
    assert work != home and "Pracovní   ● aktivní" in [tab.table.item(r, 0).text() for r in range(2)]
    cfg = ctrl.cfg.copy()
    cfg.ping.targets = [PingTarget("Firma", "10.20.30.1")]
    cfg.network.ip_profiles = [{"name": "Firma", "dhcp": False, "ip": "10.20.30.2",
                                "mask": "255.255.255.0", "gateway": "10.20.30.1", "dns_dhcp": False,
                                "dns": ["1.1.1.1"]}]
    ctrl.update_config(cfg)
    select(tab, home)
    tab._activate()
    assert ctrl.cfg.ping.targets[0].name == "Zelina"
    assert store.load(work).network.ip_profiles[0]["dns"] == ["1.1.1.1"]
    select(tab, work)
    tab._activate()
    assert ctrl.cfg.ping.targets[0].name == "Firma"
    assert load_config(ctrl.config_path).network.ip_profiles == cfg.network.ip_profiles
    assert ProfileStore(store.root).active_id() == work
    select(tab, work)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    tab._delete()                                         # aktivní profil smazat nejde
    assert len(store.list_profiles()) == 2
    select(tab, home)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    tab._delete()
    assert store.active_id() == work and len(store.list_profiles()) == 1
    d.close()


def test_switch_from_widget_menu_keeps_window_position(env):
    ctrl, store = env
    from netmonitor.gui.main_widget import MainWidget
    other = AppConfig.from_dict({})
    other.ping.targets = [PingTarget("Zákazník", "10.9.9.9")]
    other.appearance.x, other.appearance.y = 5, 5
    pid = store.create("Zákazník", other)
    mine = ctrl.cfg.copy()
    mine.appearance.x, mine.appearance.y = 700, 300
    ctrl.update_config(mine)
    w = MainWidget(ctrl)
    menu = w.build_menu(for_tray=True)
    sub = next(a.menu() for a in menu.actions() if a.menu() and a.text().startswith("Profil: Soukromý"))
    names = [a.text() for a in sub.actions() if a.isCheckable()]
    assert names == ["Soukromý", "Zákazník"]
    next(a for a in sub.actions() if a.text() == "Zákazník").trigger()
    assert store.active_id() == pid and ctrl.cfg.ping.targets[0].host == "10.9.9.9"
    assert (ctrl.cfg.appearance.x, ctrl.cfg.appearance.y) == (700, 300)
    again = w.build_menu()
    assert any(a.text().startswith("Profil: Zákazník") for a in again.actions())
    w.close()


def test_login_uploads_and_changes_sync_automatically(env, server):
    ctrl, store = env
    server.add_user()
    d = dialog(ctrl)
    assert d.tabs.indexOf(d.admin_tab) == -1
    tab = d.account_tab
    tab.email.setText("user@example.org")
    tab.password.setText(PASSWORD)
    tab._login()
    sync = ctrl.sync
    assert sync.logged_in and "user@example.org" in tab.status.text()
    assert not tab.btn_login.isEnabled() and tab.btn_logout.isEnabled()
    assert list(server.profiles()) == ["Soukromý"]
    assert d.profiles_tab.table.item(0, 2).text() == "synchronizováno"
    assert d.tabs.indexOf(d.admin_tab) == -1              # běžný uživatel správu nevidí

    cfg = ctrl.cfg.copy()
    cfg.ping.targets = [PingTarget("Nový", "10.5.5.5")]
    ctrl.update_config(cfg)
    assert sync._debounce.isActive()                      # změna naplánuje nahrání
    assert d.profiles_tab.table.item(0, 2).text() == "čeká na nahrání"
    sync._debounce.stop()
    sync.sync_now()
    assert server.profiles()["Soukromý"].config["ping"]["targets"][0]["host"] == "10.5.5.5"

    # změna z jiného počítače se stáhne a hned použije v běžícím widgetu
    other = SyncClient(URL)
    other.login("user@example.org", PASSWORD)
    remote = other.download(other.profiles()[0]["id"])
    remote["config"]["ping"]["targets"][0]["host"] = "10.6.6.6"
    other.upload(remote["id"], remote["config"], remote["revision"])
    sync.sync_now()
    assert ctrl.cfg.ping.targets[0].host == "10.6.6.6"
    assert load_config(ctrl.config_path).ping.targets[0].host == "10.6.6.6"
    sync._debounce.stop()
    sync.sync_now()
    assert server.profiles()["Soukromý"].revision == 3     # stažení nevyvolá zpětné nahrání

    tab._logout()
    assert not sync.logged_in and tab.btn_login.isEnabled()
    assert len(store.list_profiles()) == 1                 # místní profily zůstávají
    d.close()


def test_session_survives_restart_and_offline_start(env, server):
    ctrl, store = env
    server.add_user()
    login(ctrl)
    restarted = SyncService(ctrl)
    ctrl.sync = restarted
    server.offline = True
    restarted.start()
    assert "offline" in restarted.status and restarted.client.refresh_token
    server.offline = False
    restarted.start()
    assert restarted.logged_in and restarted.client.email == "user@example.org"


def test_conflict_prompt_resolves_with_user_choice(env, server, monkeypatch):
    ctrl, store = env
    server.add_user()
    sync = login(ctrl)
    asked = []
    sync.conflicts_found.connect(asked.append)
    other = SyncClient(URL)
    other.login("user@example.org", PASSWORD)
    remote = other.download(other.profiles()[0]["id"])
    remote["config"]["ping"]["targets"][0]["host"] = "10.7.7.7"
    other.upload(remote["id"], remote["config"], remote["revision"])
    cfg = ctrl.cfg.copy()
    cfg.ping.targets[0].host = "10.8.8.8"
    ctrl.update_config(cfg)
    sync._debounce.stop()
    sync.sync_now()
    assert len(asked) == 1 and len(sync.pending_conflicts) == 1
    assert ctrl.cfg.ping.targets[0].host == "10.8.8.8"
    sync.sync_now()
    assert len(asked) == 1                                 # stejný konflikt se neptá dokola

    # Dialog se nezobrazí; „kliknutí“ na tlačítko s daným textem se nasimuluje.
    picked = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda box: picked.update(button=next(
        b for b in box.buttons() if b.text() == "Použít verzi ze serveru")) or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: picked["button"])
    account_tabs.ask_conflicts(sync, asked[0])
    assert ctrl.cfg.ping.targets[0].host == "10.7.7.7" and not sync.pending_conflicts


@pytest.mark.parametrize("dismiss", ["enter", "escape", "later", "close"])
def test_conflict_can_be_deferred_and_reopened(env, monkeypatch, qapp, dismiss):
    from copy import deepcopy
    from unittest.mock import Mock

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from netmonitor.core import sync_engine

    ctrl, store = env
    sync = ctrl.sync
    cfg = ctrl.cfg.copy()
    cfg.ping.targets = [PingTarget("Test", "192.0.2.1", id="test")]
    ctrl.update_config(cfg)
    ctrl._save_timer.stop()
    ctrl._save_now()
    # Relace a konflikt bez HTTP; testuje se dialog a skutečná fronta SyncService.
    sync.client.access_token = "test-token"
    sync.client.user = {"email": "user@example.com"}
    sync.pending_conflicts = [sync_engine.Action("conflict", store.active_id(),
        {"id": "remote", "revision": 3, "updated_at": "2026-11-19T12:00:00+00:00"}, name="Soukromý")]
    pending = list(sync.pending_conflicts)
    before = store.load(store.active_id()).to_dict()
    remote_before = deepcopy(pending[0].remote)
    resolve = Mock(wraps=sync.resolve)
    sync_now = Mock()
    monkeypatch.setattr(sync, "resolve", resolve)
    monkeypatch.setattr(sync, "sync_now", sync_now)
    seen = {}

    def dismiss_dialog(box):
        box.show()
        qapp.processEvents()
        seen["default"] = next((b.text() for b in box.buttons() if b.isDefault()), None)
        seen["escape"] = box.escapeButton().text() if box.escapeButton() else None
        try:
            if dismiss in ("enter", "escape"):
                key = Qt.Key.Key_Return if dismiss == "enter" else Qt.Key.Key_Escape
                QTest.keyClick(box, key)
                qapp.processEvents()
                if box.isVisible() and sys.platform == "darwin":
                    # Na macOS simulovaný Esc dialog v testu nezavře (Qt/Cocoa); únikové tlačítko je ověřené výše.
                    box.escapeButton().click()
            elif dismiss == "later":
                next(b for b in box.buttons() if b.text() == "Rozhodnu později").click()
            else:
                box.close()
            seen["closed"] = not box.isVisible()
            return 0
        finally:
            box.hide()

    monkeypatch.setattr(QMessageBox, "exec", dismiss_dialog)
    account_tabs.ask_conflicts(sync, pending)
    assert seen == {"default": "Rozhodnu později", "escape": "Rozhodnu později", "closed": True}
    assert sync.pending_conflicts == pending and not sync._resolutions
    resolve.assert_not_called()
    sync_now.assert_not_called()
    assert store.load(store.active_id()).to_dict() == before
    assert ctrl.cfg.ping.targets[0].host == "192.0.2.1"
    assert pending[0].remote == remote_before

    # Odložený konflikt jde znovu otevřít z karty Profily a výslovně vyřešit.
    tab = account_tabs.ProfilesTab(ctrl)
    try:
        assert not tab.btn_conflicts.isHidden()

        def choose_remote(box):
            next(b for b in box.buttons() if b.text() == "Použít verzi ze serveru").click()
            return 0

        monkeypatch.setattr(QMessageBox, "exec", choose_remote)
        tab.btn_conflicts.click()
        resolve.assert_called_once_with(store.active_id(), sync_engine.KEEP_REMOTE)
        sync_now.assert_called_once_with()
        assert not sync.pending_conflicts
    finally:
        tab.close()


def test_register_with_license_key_and_admin_tab(env, server, monkeypatch):
    ctrl, store = env
    secret = server.add_admin()
    d = dialog(ctrl)
    tab = d.account_tab
    tab.email.setText("admin@example.org")
    tab.password.setText(PASSWORD)
    tab._login()
    assert not ctrl.sync.logged_in and "kód" in tab.status.text().lower()
    tab.code.setText(server.code(secret))
    tab._login()
    assert ctrl.sync.logged_in and d.tabs.indexOf(d.admin_tab) >= 0
    admin = d.admin_tab
    assert admin.keys_box.isEnabled() and admin.totp_box.isHidden()
    admin._create_key()
    assert "poznámku" in admin.status.text()               # klíč bez poznámky nevznikne
    admin.note.setText("Jana – účetní")
    admin._create_key()
    key = admin.new_key.text()
    assert key.startswith("NM-") and admin.keys.item(0, 0).text() == "Jana – účetní"
    assert admin.keys.item(0, 2).text() == "platný"
    tab._logout()
    assert d.tabs.indexOf(d.admin_tab) == -1

    tab.email.setText("jana@example.org")
    tab.password.setText(PASSWORD)
    tab._register()
    assert "licenční klíč" in tab.status.text()
    tab.license.setText(key)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("jine-heslo-123456", True))
    tab._register()
    assert "neshodují" in tab.status.text() and not ctrl.sync.logged_in
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (PASSWORD, True))
    tab._register()
    assert ctrl.sync.logged_in and ctrl.sync.client.email == "jana@example.org"
    # Profil na tomto počítači patří účtu správce – do cizího účtu se sám nenahraje.
    assert server.profiles("jana@example.org") == {}
    assert d.profiles_tab.table.item(0, 2).text() == "patří k jinému účtu"
    assert not d.profiles_tab.btn_reupload.isHidden()
    d.profiles_tab._reupload()                             # výslovné „Nahrát do tohoto účtu“
    ctrl.sync._debounce.stop()
    ctrl.sync.sync_now()
    assert list(server.profiles("jana@example.org")) == ["Soukromý"]
    d.close()


def test_admin_without_totp_gets_server_command_not_web_enrollment(env, server):
    ctrl, _store = env
    server.add_user("admin@example.org", admin=True)
    login(ctrl, "admin@example.org")
    d = dialog(ctrl)
    admin = d.admin_tab
    assert d.tabs.indexOf(admin) >= 0 and not admin.keys_box.isEnabled() and not admin.totp_box.isHidden()
    assert "app.cli totp-setup admin@example.org" in admin.totp_hint.text()
    d.close()


def test_wrong_password_and_unreachable_server_keep_local_profiles(env, server):
    ctrl, store = env
    server.add_user()
    before = store.load(store.active_id()).to_dict()
    result = []
    ctrl.sync.login("user@example.org", "spatne-heslo-12345", None, True, result.append)
    assert result[0].code == "invalid_credentials" and not ctrl.sync.logged_in
    login(ctrl)
    server.offline = True
    cfg = ctrl.cfg.copy()
    cfg.ping.targets[0].name = "Offline změna"
    ctrl.update_config(cfg)
    ctrl.sync._debounce.stop()
    ctrl.sync.sync_now()
    assert "odložena" in ctrl.sync.status and ctrl.sync.logged_in
    server.offline = False
    ctrl.sync.sync_now()
    assert server.profiles()["Soukromý"].config["ping"]["targets"][0]["name"] == "Offline změna"
    assert store.load(store.active_id()).to_dict() != before
