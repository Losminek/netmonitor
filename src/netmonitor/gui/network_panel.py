"""Horní část widgetu: WAN IP + stav, ISP, adaptér, LAN IP, brána, rychlost, SSID, DNS."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ..core.config import AppConfig
from ..core.connection_status import ConnEvaluation, ConnState
from ..core.network_info import NetworkSnapshot
from . import theme
from .i18n import tr
from .widgets import NeonSeparator, ValueLabel

NA = "N/A"


def fmt_speed(mbps: float | None) -> str:
    if not mbps:
        return NA
    if mbps >= 1000:
        g = mbps / 1000
        return f"{g:.1f} Gb/s".replace(".0 ", " ")
    return f"{mbps:.0f} Mb/s"


def fmt_ts(ts: float | None) -> str:
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(ts)) if ts else "–"


class _Row:
    def __init__(self, label: str):
        self.label = QLabel(label)
        self.colon = QLabel(":")
        self.value = ValueLabel(NA)
        for w in (self.label, self.colon):
            w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            w.setStyleSheet(f"color: {theme.LABEL.name()}; background: transparent;")

    def widgets(self):
        return self.label, self.colon, self.value

    def set_visible(self, v: bool) -> None:
        for w in self.widgets():
            w.setVisible(v)


class NetworkPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- blok 1: WAN IP / LAN stav / ISP
        self.wan = _Row("WAN IP")
        self.lan = _Row("LAN")
        self.isp = _Row("ISP")
        g1 = self._grid()
        self._place(g1, 0, self.wan, 0)
        self._place(g1, 0, self.lan, 4)
        self._place(g1, 1, self.isp, 0, span=5)
        self.isp.value.set_value(NA, theme.CYAN_TEXT)
        root.addLayout(g1)
        self.sep1 = NeonSeparator()
        root.addWidget(self.sep1)

        # --- blok 2: adaptér
        self.adapter = _Row("Wireless")
        self.lanip = _Row("LAN IP")
        self.speed = _Row("Speed")
        self.gw = _Row("GW")
        self.ssid = _Row("SSID")
        self.ipv6 = _Row("IPv6")
        g2 = self._grid()
        self._place(g2, 0, self.adapter, 0)
        self._place(g2, 0, self.lanip, 4)
        self._place(g2, 1, self.speed, 0)
        self._place(g2, 1, self.gw, 4)
        self._place(g2, 2, self.ssid, 0, span=5)
        self._place(g2, 3, self.ipv6, 0, span=5)
        self.speed.label.setToolTip(tr("Linková rychlost adaptéru – není to rychlost internetu"))
        self.speed.value.setToolTip(tr("Linková rychlost adaptéru – není to rychlost internetu"))
        self.ipv6.set_visible(False)
        root.addLayout(g2)
        self.sep2 = NeonSeparator()
        root.addWidget(self.sep2)

        # --- blok 3: DNS
        self.dns_rows = [_Row("IP4.DNS[1]"), _Row("IP4.DNS[2]")]
        self.g3 = self._grid()
        for i, r in enumerate(self.dns_rows):
            self._place(self.g3, i, r, 0, span=5)
        root.addLayout(self.g3)
        self.sep3 = NeonSeparator()
        root.addWidget(self.sep3)

        self._cfg: AppConfig | None = None
        self._font = QFont()
        self._all_rows = [self.wan, self.lan, self.isp, self.adapter, self.lanip, self.speed, self.gw, self.ssid,
                          self.ipv6, *self.dns_rows]
        self.lan.value.set_value("UNKNOWN", theme.MUTED)

    def _grid(self) -> QGridLayout:
        g = QGridLayout()
        g.setContentsMargins(6, 4, 6, 4)
        g.setHorizontalSpacing(4)
        g.setVerticalSpacing(1)
        g.setColumnStretch(2, 10)
        g.setColumnMinimumWidth(3, 12)
        g.setColumnStretch(6, 8)
        return g

    def _place(self, g: QGridLayout, row: int, r: _Row, col: int, span: int = 1) -> None:
        g.addWidget(r.label, row, col)
        g.addWidget(r.colon, row, col + 1)
        g.addWidget(r.value, row, col + 2, 1, span)

    # ------------------------------------------------------------------
    def apply_style(self, cfg: AppConfig, scale: float) -> None:
        self._cfg = cfg
        self._font = theme.font("body", 21 if not cfg.appearance.compact else 17, QFont.Weight.Medium, scale)
        for r in self._all_rows:
            for w in r.widgets():
                w.setFont(self._font)
        self._align_columns()
        for s in (self.sep1, self.sep2, self.sep3):
            s.set_style(scale, cfg.appearance.neon_effects)

    def _align_columns(self) -> None:
        """Pevné šířky sloupců popisků => dvojtečky jsou zarovnané pod sebou (jako v návrhu)."""
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self._font)
        blocks = [
            ([self.wan, self.isp], ["WAN IP", "ISP"]),
            ([self.lan], ["LAN"]),
            ([self.adapter, self.speed, self.ssid, self.ipv6], ["Wireless", "Ethernet", "Adapter", "Speed", "SSID", "IPv6"]),
            ([self.lanip, self.gw], ["LAN IP", "GW"]),
            (self.dns_rows, ["IP4.DNS[1]", "IP4.DNS[2]"]),
        ]
        colon_w = fm.horizontalAdvance(" : ")
        for rows, texts in blocks:
            w = max(fm.horizontalAdvance(t) for t in texts) + 4
            for r in rows:
                r.label.setFixedWidth(w)
                r.colon.setFixedWidth(colon_w)
                r.colon.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def update_connection(self, ev: ConnEvaluation, colors) -> None:
        kind = {ConnState.ONLINE: "ok", ConnState.DEGRADED: "warn", ConnState.OFFLINE: "bad"}.get(ev.state, "unk")
        lay = ev.layers or {}
        tip = (f"Stav: {ev.state.value}\n{ev.reason}\n\nRozhraní: {_yn(lay.get('interface'))}\n"
               f"Brána (ping): {_yn(lay.get('gateway'))}\nInternetové cíle OK: {lay.get('internet_targets', '–')}")
        self.lan.value.set_value(ev.state.value, theme.status_color(colors, kind), tip)

    def update_wan(self, info: dict) -> None:
        if info.get("disabled"):
            self.wan.value.set_value("vypnuto", theme.MUTED, "Externí dotazy jsou v nastavení vypnuté")
            self.isp.value.set_value(NA, theme.MUTED, "Externí dotazy jsou v nastavení vypnuté")
            return
        ip = info.get("ip")
        tip = (f"Předchozí: {info.get('previous') or '–'}\nZměněno: {fmt_ts(info.get('changed_at'))}\n"
               f"Poslední kontrola: {fmt_ts(info.get('last_check'))}")
        if info.get("error") and not ip:
            tip += f"\nChyba: {info['error'][:200]}"
        self.wan.value.set_value(ip or NA, theme.WHITE if ip else theme.MUTED, tip)
        isp = info.get("isp")
        self.isp.value.set_value(isp or NA, theme.CYAN_TEXT if isp else theme.MUTED)

    def update_network(self, snap: NetworkSnapshot, show_ipv6: bool) -> None:
        a = snap.active
        if a is None:
            self.adapter.label.setText("Adapter")
            for r in (self.adapter, self.lanip, self.speed, self.gw):
                r.value.set_value(NA, theme.MUTED)
            self.ssid.set_visible(False)
            for r in self.dns_rows:
                r.value.set_value(NA, theme.MUTED)
            return
        if self.adapter.label.text() != a.kind_label:
            self.adapter.label.setText(a.kind_label)
        self.adapter.value.set_value(a.name, theme.WHITE,
                                     f"{a.display_name}\nTyp: {a.kind}\nMAC: {a.mac or '–'}\n"
                                     f"Stav: {'UP' if a.is_up else 'DOWN'}")
        self.lanip.value.set_value(a.ipv4[0] if a.ipv4 else NA, theme.WHITE if a.ipv4 else theme.MUTED,
                                   "\n".join(a.ipv4 + a.ipv6) or None)
        self.gw.value.set_value(a.gateway or NA, theme.WHITE if a.gateway else theme.MUTED)
        self.speed.value.set_value(fmt_speed(a.speed_mbps), theme.WHITE if a.speed_mbps else theme.MUTED)
        is_wifi = a.kind == "wifi"
        self.ssid.set_visible(is_wifi)
        if is_wifi:
            text = a.ssid or NA
            if a.ssid and a.signal_pct is not None:
                text += f"   ({a.signal_pct} %)"
            elif a.ssid and a.signal_dbm is not None:
                text += f"   ({a.signal_dbm} dBm)"
            tip = a.wifi_note or (f"Signál: {a.signal_pct} %" if a.signal_pct is not None else None)
            self.ssid.value.set_value(text, theme.WHITE if a.ssid else theme.MUTED, tip or "")
        v6 = [x for x in a.ipv6 if not x.lower().startswith("fe80")]
        self.ipv6.set_visible(show_ipv6)
        if show_ipv6:
            self.ipv6.value.set_value(v6[0] if v6 else NA, theme.WHITE if v6 else theme.MUTED, "\n".join(a.ipv6))

        v4dns = [d for d in a.dns if ":" not in d]
        v6dns = [d for d in a.dns if ":" in d]
        tip = a.dns_note + ("\nVšechny DNS:\n" + "\n".join(a.dns) if a.dns else "")
        for i, r in enumerate(self.dns_rows):
            val = v4dns[i] if i < len(v4dns) else NA
            note = ""
            if val in ("127.0.0.53", "127.0.0.54"):
                note = "  (lokální stub)"
            r.value.set_value(val + note, theme.WHITE if i < len(v4dns) else theme.MUTED, tip)
        if show_ipv6 and v6dns:   # IPv6 DNS do tooltipu řádku IPv6
            self.ipv6.value.setToolTip("\n".join(a.ipv6) + "\nIPv6 DNS:\n" + "\n".join(v6dns))


def _yn(v) -> str:
    return {True: "OK", False: "nedostupné", None: "neměřeno"}.get(v, str(v))
