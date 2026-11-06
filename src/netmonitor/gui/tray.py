"""Ikona v oznamovací oblasti – stav připojení barvou, menu, lokální upozornění."""
from __future__ import annotations

from PySide6.QtWidgets import QSystemTrayIcon

from ..core.connection_status import ConnEvaluation, ConnState
from ..core.notifier import Notification
from .icons import app_icon


class Tray(QSystemTrayIcon):
    def __init__(self, widget):
        super().__init__(app_icon("unknown"))
        self.widget = widget
        self._state = None
        self.setToolTip("NetMonitor")
        self._menu = None
        self._rebuild_menu()
        self.activated.connect(self._on_activated)
        widget.ctrl.connection_changed.connect(self.on_connection)
        widget.ctrl.notification.connect(self.on_notification)
        widget.visibility_toggled.connect(lambda _v: self._rebuild_menu())
        widget.ctrl.config_changed.connect(lambda _c: self._rebuild_menu())

    def _rebuild_menu(self) -> None:
        old = self._menu
        self._menu = self.widget.build_menu(for_tray=True)
        self.setContextMenu(self._menu)
        if old is not None:
            old.deleteLater()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.widget.toggle_visible()

    def on_connection(self, ev: ConnEvaluation) -> None:
        state = {ConnState.ONLINE: "ok", ConnState.DEGRADED: "warn", ConnState.OFFLINE: "bad"}.get(ev.state, "unk")
        if state != self._state:
            self._state = state
            self.setIcon(app_icon(state))
        wan = self.widget.ctrl.wan.current or "N/A"
        self.setToolTip(f"NetMonitor – {ev.state.value}\nWAN: {wan}\n{ev.reason}"[:120])

    def on_notification(self, n: Notification) -> None:
        icon = QSystemTrayIcon.MessageIcon.Warning if n.critical else QSystemTrayIcon.MessageIcon.Information
        self.showMessage(n.title, n.message, icon, 8000)
