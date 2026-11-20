"""Karta Správa: sloupce zařízení a IP, dialog zařízení účtu a odhlášení zařízení."""
from PySide6.QtWidgets import QMessageBox

from netmonitor.core.sync_client import SyncClient, SyncError
from netmonitor.gui.account_tabs import DevicesDialog, selected

from .sync_server_fake import PASSWORD, URL, server  # noqa: F401
from .test_gui_smoke import ctrl, pump, qapp  # noqa: F401
from .test_profile_workflow import dialog, env, login  # noqa: F401


def test_admin_sees_last_ip_and_can_log_out_a_device(env, server, monkeypatch):
    ctrl, _store = env
    secret = server.add_admin()
    server.add_user("pavel@example.org")
    pavel = SyncClient(URL)
    pavel.login("pavel@example.org", PASSWORD)
    login(ctrl, "admin@example.org", server.code(secret))
    d = dialog(ctrl)
    admin = d.admin_tab
    admin.refresh()
    headers = [admin.users.horizontalHeaderItem(i).text() for i in range(admin.users.columnCount())]
    assert headers == ["E-mail", "Klíč (poznámka)", "Profilů", "Zařízení", "Poslední aktivita", "Poslední IP", "Stav"]
    rows = {admin.users.item(r, 0).text(): r for r in range(admin.users.rowCount())}
    r = rows["pavel@example.org"]
    assert admin.users.item(r, 3).text() == "1" and admin.users.item(r, 5).text() not in ("", "—")

    admin.users.setCurrentCell(r, 0)
    user = selected(admin.users)
    devices = DevicesDialog(ctrl.sync, user, d)
    assert devices.table.rowCount() == 1 and devices.table.item(0, 4).text() == "přihlášeno"
    devices.table.setCurrentCell(0, 0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    devices._revoke()
    assert devices.table.item(0, 4).text() == "ukončeno"
    assert "ukončených: 1" in devices.status.text()
    try:
        pavel.profiles()
        raise AssertionError("odhlášené zařízení nemá mít přístup")
    except SyncError as exc:
        assert exc.status == 401
    devices.close()
    d.close()
