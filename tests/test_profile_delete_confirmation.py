"""L49: skutečný text potvrzení smazání pro místní i synchronizovaný profil."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from netmonitor.core.config import AppConfig, PingTarget
from netmonitor.core.profiles import ProfileStore
from netmonitor.gui.account_tabs import ProfilesTab

from .test_gui_smoke import qapp  # noqa: F401


class Events(QObject):
    profiles_changed = Signal()
    network_identified = Signal()
    config_changed = Signal(object)


@pytest.mark.parametrize("synced", [True, False], ids=["synchronizovany", "mistni"])
def test_delete_confirmation_describes_actual_scope(qapp, tmp_path, monkeypatch, synced):
    cfg = AppConfig.from_dict({"network": {"external_api": False}})
    cfg.ping.targets = [PingTarget("Test", "192.0.2.1")]
    store = ProfileStore(tmp_path / "profiles")
    store.initialize(cfg)
    pid = store.create("Testovací", cfg)
    remote = str(uuid4())
    if synced:
        store.bind_remote(pid, remote, 2, "test@example.com", "https://example.com")
    events = Events()
    ctrl = SimpleNamespace(cfg=cfg, profile_store=store, sync=None, net_identity=None,
                           profiles_changed=events.profiles_changed,
                           network_identified=events.network_identified, config_changed=events.config_changed)
    tab = ProfilesTab(ctrl)
    tab.refresh(pid)
    messages = []

    def question(parent, title, text):
        messages.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    try:
        tab._delete()
        assert len(messages) == 1
        text = messages[0]
        assert "Opravdu smazat profil „Testovací“?" in text
        assert store.record(pid)["name"] == "Testovací"  # odmítnutí nic nemaže
        if synced:
            assert "při nejbližší synchronizaci" in text
            assert "ze serveru" in text
            assert "na ostatních počítačích zůstane jako „jen místní“" in text
        else:
            assert "jen z tohoto počítače" in text
            assert "serveru" not in text and "ostatních počítačích" not in text
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        tab._delete()
        assert pid not in {p["id"] for p in store.list_profiles()}
        assert not store._file(pid).exists()
        assert store.pending_deletes("test@example.com", "https://example.com") == ([(remote, 2)] if synced else [])
    finally:
        tab.close()
