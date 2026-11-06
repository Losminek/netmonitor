"""Dialog nastavení. Každá změna se okamžitě promítne do widgetu i monitoringu
(a s krátkou prodlevou uloží do config.json)."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QSlider, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import __version__
from .. import platform as plat
from ..core import config as cfgmod
from ..core.config import ICON_CHOICES, MODE_DESKTOP, MODE_ONTOP, AppConfig, PingTarget, default_targets
from ..utils.paths import config_dir, data_dir, log_dir
from ..utils.validation import host_kind
from . import theme
from .controller import Controller

log = logging.getLogger(__name__)

ICON_LABELS = {"auto": "Automaticky", "globe": "Globus (web)", "server": "Server", "dns": "DNS / databáze",
               "wifi": "Wi-Fi / anténa", "router": "Router", "modem": "Modem", "box": "Box", "cloud": "Cloud"}


def hint(text: str) -> QLabel:
    l = QLabel(text)
    l.setWordWrap(True)
    l.setProperty("hint", True)
    return l


class TargetDialog(QDialog):
    """Přidání/úprava jednoho cíle s validací."""

    def __init__(self, target: PingTarget | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Upravit cíl" if target else "Přidat cíl")
        self.setStyleSheet(theme.DIALOG_QSS)
        self.t = target or PingTarget(name="", host="")
        f = QFormLayout(self)
        self.name = QLineEdit(self.t.name)
        self.host = QLineEdit(self.t.host)
        self.host.setPlaceholderText("např. 8.8.8.8, 2001:4860:4860::8888 nebo seznam.cz")
        self.enabled = QCheckBox("Aktivní")
        self.enabled.setChecked(self.t.enabled)
        self.interval = QDoubleSpinBox(minimum=1, maximum=60, decimals=1, singleStep=0.5, suffix=" s")
        self.interval.setValue(self.t.interval_s)
        self.timeout = QDoubleSpinBox(minimum=0.2, maximum=10, decimals=1, singleStep=0.1, suffix=" s")
        self.timeout.setValue(self.t.timeout_s)
        self.retries = QSpinBox(minimum=1, maximum=20)
        self.retries.setValue(self.t.retries)
        self.icon = QComboBox()
        for k in ICON_CHOICES:
            self.icon.addItem(ICON_LABELS[k], k)
        self.icon.setCurrentIndex(ICON_CHOICES.index(self.t.icon))
        self.show_rtt = QCheckBox("Zobrazovat odezvu v ms (jinak 'OK')")
        self.show_rtt.setChecked(self.t.show_rtt)
        self.probe = QComboBox()
        for k, lab in (("auto", "Automaticky (veřejné adresy ano)"), ("yes", "Ano"), ("no", "Ne")):
            self.probe.addItem(lab, k)
        self.probe.setCurrentIndex(["auto", "yes", "no"].index(self.t.internet_probe))
        self.err = QLabel()
        self.err.setProperty("error", True)
        self.err.setWordWrap(True)
        f.addRow("Název", self.name)
        f.addRow("IP / doména", self.host)
        f.addRow("", self.enabled)
        f.addRow("Interval", self.interval)
        f.addRow("Timeout", self.timeout)
        f.addRow("Opakování před výpadkem", self.retries)
        f.addRow("Ikona", self.icon)
        f.addRow("", self.show_rtt)
        f.addRow("Počítat do stavu internetu", self.probe)
        f.addRow(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.host.textChanged.connect(self._live_validate)
        self.resize(460, 0)

    def _live_validate(self, text: str) -> None:
        bad = bool(text.strip()) and host_kind(text) == "invalid"
        self.host.setProperty("invalid", bad)
        self.host.style().unpolish(self.host)
        self.host.style().polish(self.host)

    def result_target(self) -> PingTarget:
        return PingTarget(name=self.name.text().strip() or self.host.text().strip(), host=self.host.text().strip(),
                          enabled=self.enabled.isChecked(), interval_s=self.interval.value(),
                          timeout_s=self.timeout.value(), retries=self.retries.value(),
                          icon=self.icon.currentData(), show_rtt=self.show_rtt.isChecked(),
                          internet_probe=self.probe.currentData(), id=self.t.id)

    def _accept(self) -> None:
        errs = self.result_target().validate()
        if errs:
            self.err.setText("\n".join(errs))
            return
        self.accept()


class SettingsDialog(QDialog):
    history_requested = Signal(str)

    def __init__(self, ctrl: Controller, main_widget, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.mw = main_widget
        self.setWindowTitle(f"NetMonitor {__version__} – Nastavení")
        self.setStyleSheet(theme.DIALOG_QSS)
        self.resize(820, 640)
        self._loading = False
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.tabs.addTab(self._tab_general(), "Obecné")
        self.tabs.addTab(self._tab_appearance(), "Vzhled")
        self.tabs.addTab(self._tab_network(), "Síť")
        self.tabs.addTab(self._tab_targets(), "Ping cíle")
        self.tabs.addTab(self._tab_notifications(), "Upozornění")
        self.tabs.addTab(self._tab_history(), "Historie")
        bottom = QHBoxLayout()
        self.status = hint("Změny se projeví okamžitě a automaticky se uloží.")
        bottom.addWidget(self.status, 1)
        close = QPushButton("Zavřít")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        ctrl.config_changed.connect(self._on_external_change)
        self.load()

    # ------------------------------------------------------------------ pomocné
    def _apply(self, fn: Callable[[AppConfig], None]) -> None:
        if self._loading:
            return
        cfg = self.ctrl.cfg.copy()
        fn(cfg)
        self.ctrl.update_config(cfg)

    def _setter(self, section: str, key: str, conv=lambda v: v):
        return lambda v: self._apply(lambda c: setattr(getattr(c, section), key, conv(v)))

    def _check(self, text: str, section: str, key: str) -> QCheckBox:
        cb = QCheckBox(text)
        cb.toggled.connect(self._setter(section, key, bool))
        self._binds.append((cb, section, key))
        return cb

    def _spin(self, section: str, key: str, lo, hi, suffix: str = "", step=1, decimals: int | None = None):
        sp = QDoubleSpinBox(decimals=decimals) if decimals is not None else QSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        if suffix:
            sp.setSuffix(suffix)
        sp.setKeyboardTracking(False)
        sp.valueChanged.connect(self._setter(section, key, float if decimals is not None else int))
        self._binds.append((sp, section, key))
        return sp

    _binds: list = []

    def _on_external_change(self, cfg: AppConfig) -> None:
        if not self.isVisible() or self._loading:
            return
        self.load()

    def load(self) -> None:
        """Načte hodnoty z aktuální konfigurace do ovládacích prvků (bez vyvolání změn)."""
        self._loading = True
        try:
            c = self.ctrl.cfg
            for w, section, key in self._binds:
                v = getattr(getattr(c, section), key)
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    w.setValue(v)
            g, a, n = c.general, c.appearance, c.network
            self.lang.setCurrentIndex(0 if g.language == "cs" else 1)
            self.update_url.setText(g.update_url)
            self.rb_desktop.setChecked(a.mode == MODE_DESKTOP)
            self.rb_ontop.setChecked(a.mode == MODE_ONTOP)
            self.opacity.setValue(int(a.opacity * 100))
            self.glass.setValue(int(a.glass_alpha * 100))
            self.w_spin.setValue(self.mw.width())
            self.h_spin.setValue(self.mw.height())
            for k, b in self.color_btns.items():
                self._paint_color_btn(b, getattr(a.colors, k))
            self.notice.setText(self.mw.platform_notice)
            self.notice.setVisible(bool(self.mw.platform_notice))
            self.rb_auto.setChecked(n.adapter_auto)
            self.rb_manual.setChecked(not n.adapter_auto)
            self._fill_adapters()
            self.isp_provider.setCurrentIndex(0 if n.isp_provider == "ipinfo" else 1)
            self.green.setValue(c.ping.thresholds.green_max_ms)
            self.orange.setValue(c.ping.thresholds.orange_max_ms)
            self.pinger_info.setText(f"Použitá ICMP implementace: <b>{self.ctrl.pinger.name}</b>")
            self._fill_table()
        finally:
            self._loading = False

    # ------------------------------------------------------------------ Obecné
    def _tab_general(self) -> QWidget:
        self._binds = []
        w = QWidget()
        f = QFormLayout(w)
        self.lang = QComboBox()
        self.lang.addItems(["Čeština", "English (připraveno – částečný překlad)"])
        self.lang.currentIndexChanged.connect(
            lambda i: self._apply(lambda c: setattr(c.general, "language", "cs" if i == 0 else "en")))
        f.addRow("Jazyk", self.lang)
        f.addRow(hint("Změna jazyka se plně projeví po restartu aplikace."))
        self.autostart = QCheckBox("Spustit automaticky po přihlášení")
        self.autostart.toggled.connect(self._toggle_autostart)
        self._binds.append((self.autostart, "general", "autostart"))
        f.addRow(self.autostart)
        f.addRow(self._check("Spouštět minimalizované (jen ikona v oznamovací oblasti)", "general", "start_minimized"))
        f.addRow(self._check("Při skrytí minimalizovat do oznamovací oblasti", "general", "minimize_to_tray"))
        f.addRow(self._check("Kontrolovat novou verzi (volitelné, vyžaduje URL)", "general", "check_updates"))
        self.update_url = QLineEdit()
        self.update_url.setPlaceholderText("https://…/netmonitor-latest.json  (prázdné = vypnuto)")
        self.update_url.editingFinished.connect(
            lambda: self._apply(lambda c: setattr(c.general, "update_url", self.update_url.text().strip())))
        f.addRow("URL informace o verzi", self.update_url)
        row = QHBoxLayout()
        for label, path in (("Složka konfigurace", config_dir()), ("Složka dat", data_dir()), ("Logy", log_dir())):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
            row.addWidget(b)
        f.addRow(row)
        f.addRow(hint(f"Konfigurace: {cfgmod.default_config_path()}\nBez telemetrie, bez účtů, "
                      f"veškerá data zůstávají v tomto počítači."))
        return w

    def _toggle_autostart(self, on: bool) -> None:
        if self._loading:
            return
        try:
            plat.current().set_autostart(on, self.ctrl.autostart_command())
            self._apply(lambda c: setattr(c.general, "autostart", on))
            self.status.setText("Automatické spuštění " + ("zapnuto." if on else "vypnuto."))
        except Exception as e:  # noqa: BLE001
            log.exception("Autostart")
            QMessageBox.warning(self, "Autostart", f"Nepodařilo se nastavit automatické spuštění:\n{e}")

    # ------------------------------------------------------------------ Vzhled
    def _tab_appearance(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        gm = QGroupBox("Režim zobrazení")
        gl = QVBoxLayout(gm)
        self.rb_desktop = QRadioButton("A – Desktop widget (pod běžnými okny, bez titulku)")
        self.rb_ontop = QRadioButton("B – Vždy navrchu")
        self.rb_desktop.toggled.connect(lambda on: on and self._apply(lambda c: setattr(c.appearance, "mode", MODE_DESKTOP)))
        self.rb_ontop.toggled.connect(lambda on: on and self._apply(lambda c: setattr(c.appearance, "mode", MODE_ONTOP)))
        gl.addWidget(self.rb_desktop)
        gl.addWidget(self.rb_ontop)
        self.notice = hint("")
        gl.addWidget(self.notice)
        v.addWidget(gm)

        f = QFormLayout()
        self.opacity = QSlider(Qt.Orientation.Horizontal, minimum=20, maximum=100)
        self.opacity.valueChanged.connect(lambda x: self._apply(lambda c: setattr(c.appearance, "opacity", x / 100)))
        f.addRow("Průhlednost celého widgetu", self.opacity)
        self.glass = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.glass.valueChanged.connect(lambda x: self._apply(lambda c: setattr(c.appearance, "glass_alpha", x / 100)))
        f.addRow("Krytí skleněného pozadí", self.glass)
        size = QHBoxLayout()
        self.w_spin = QSpinBox(minimum=360, maximum=2000, suffix=" px", keyboardTracking=False)
        self.h_spin = QSpinBox(minimum=360, maximum=2400, suffix=" px", keyboardTracking=False)
        self.w_spin.valueChanged.connect(lambda _: self._resize_from_spins())
        self.h_spin.valueChanged.connect(lambda _: self._resize_from_spins())
        size.addWidget(self.w_spin)
        size.addWidget(QLabel("×"))
        size.addWidget(self.h_spin)
        reset = QPushButton("Výchozí 600 × 850")
        reset.clicked.connect(lambda: (self.w_spin.setValue(600), self.h_spin.setValue(850)))
        size.addWidget(reset)
        f.addRow("Velikost (logické px, respektuje škálování)", size)
        pos = QPushButton("Přesunout do pravého horního rohu")
        pos.clicked.connect(self._reset_position)
        f.addRow("Pozice", pos)
        f.addRow(self._check("Uzamknout pozici a velikost", "appearance", "locked"))
        ct = self._check("Ignorovat kliknutí myší (click-through) – vypnout lze z ikony v oznamovací oblasti",
                         "appearance", "click_through")
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance().property("tray_available"):
            ct.setEnabled(False)
            ct.setToolTip("Není dostupná oznamovací oblast – click-through by nešel vypnout")
        f.addRow(ct)
        f.addRow(self._check("Neonové efekty (záře)", "appearance", "neon_effects"))
        f.addRow(self._check("Kompaktní režim", "appearance", "compact"))
        f.addRow("Velikost písma", self._spin("appearance", "font_scale", 0.6, 2.0, " ×", 0.05, 2))
        colors = QHBoxLayout()
        self.color_btns = {}
        for k, label in (("ok", "OK"), ("warn", "Varování"), ("bad", "Chyba"), ("unknown", "Neznámé")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=k: self._pick_color(k))
            self.color_btns[k] = b
            colors.addWidget(b)
        f.addRow("Barvy stavů", colors)
        v.addLayout(f)
        v.addStretch(1)
        return w

    def _resize_from_spins(self) -> None:
        if self._loading:
            return
        self.mw.set_size(self.w_spin.value(), self.h_spin.value())

    def _reset_position(self) -> None:
        scr = self.mw.screen().availableGeometry()
        self.mw.move(scr.right() - self.mw.width() - 40, scr.top() + 40)
        self.mw.remember_geometry()

    def _paint_color_btn(self, b: QPushButton, color: str) -> None:
        b.setStyleSheet(f"QPushButton{{border:2px solid {color}; color:{color};}}")

    def _pick_color(self, key: str) -> None:
        cur = QColor(getattr(self.ctrl.cfg.appearance.colors, key))
        c = QColorDialog.getColor(cur, self, "Barva stavu")
        if c.isValid():
            self._apply(lambda cfg: setattr(cfg.appearance.colors, key, c.name()))
            self._paint_color_btn(self.color_btns[key], c.name())

    # ------------------------------------------------------------------ Síť
    def _tab_network(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        ga = QGroupBox("Síťový adaptér")
        fa = QFormLayout(ga)
        self.rb_auto = QRadioButton("Automaticky (rozhraní s výchozí trasou)")
        self.rb_manual = QRadioButton("Ručně:")
        self.adapters = QComboBox()
        self.rb_auto.toggled.connect(lambda on: self._apply(lambda c: setattr(c.network, "adapter_auto", on)))
        self.adapters.currentIndexChanged.connect(self._adapter_chosen)
        fa.addRow(self.rb_auto)
        m = QHBoxLayout()
        m.addWidget(self.rb_manual)
        m.addWidget(self.adapters, 1)
        fa.addRow(m)
        fa.addRow("Obnovovat síťové údaje každých", self._spin("network", "refresh_s", 2, 300, " s"))
        fa.addRow(self._check("Zobrazit IPv6", "network", "show_ipv6"))
        fa.addRow(self._check("Pingovat výchozí bránu (pro rozlišení LAN × internet)", "network", "gateway_probe"))
        if sys.platform.startswith("linux"):
            fa.addRow(self._check("Na Waylandu použít XWayland (pozice a režim okna) – po restartu",
                                  "network", "linux_prefer_xwayland"))
        v.addWidget(ga)

        ge = QGroupBox("Externí služby (WAN IP, ISP)")
        fe = QFormLayout(ge)
        fe.addRow(self._check("Povolit externí dotazy (HTTPS, bez klíče, bez osobních dat)", "network", "external_api"))
        fe.addRow("Interval aktualizace WAN IP", self._spin("network", "wan_interval_s", 30, 86400, " s", 30))
        fe.addRow(self._check("Detekovat ISP / ASN", "network", "isp_detection"))
        self.isp_provider = QComboBox()
        self.isp_provider.addItems(["ipinfo.io (formát 'AS… název')", "RIPEstat (RIPE NCC)"])
        self.isp_provider.currentIndexChanged.connect(
            lambda i: self._apply(lambda c: setattr(c.network, "isp_provider", "ipinfo" if i == 0 else "ripestat")))
        fe.addRow("Zdroj ISP", self.isp_provider)
        fe.addRow("Cache ISP", self._spin("network", "isp_cache_hours", 1, 720, " h"))
        fe.addRow(hint("WAN IP: api.ipify.org → ipv4.icanhazip.com → checkip.amazonaws.com → ifconfig.me "
                       "(záložní řetězec). Podrobnosti v docs/EXTERNAL_APIS.md."))
        v.addWidget(ge)

        gp = QGroupBox("Ping")
        fp = QFormLayout(gp)
        self.green = QDoubleSpinBox(minimum=1, maximum=10000, suffix=" ms", decimals=0, keyboardTracking=False)
        self.orange = QDoubleSpinBox(minimum=1, maximum=20000, suffix=" ms", decimals=0, keyboardTracking=False)
        self.green.valueChanged.connect(lambda x: self._apply(lambda c: setattr(c.ping.thresholds, "green_max_ms", x)))
        self.orange.valueChanged.connect(lambda x: self._apply(lambda c: setattr(c.ping.thresholds, "orange_max_ms", x)))
        fp.addRow("Zelená do", self.green)
        fp.addRow("Oranžová do (nad = červená)", self.orange)
        fp.addRow("Max. současných pingů", self._spin("ping", "max_concurrent", 1, 128))
        self.pinger_info = QLabel()
        fp.addRow(self.pinger_info)
        v.addWidget(gp)
        v.addStretch(1)
        return w

    def _fill_adapters(self) -> None:
        self.adapters.blockSignals(True)
        self.adapters.clear()
        names = self.ctrl.net_provider.list_interfaces()
        cur = self.ctrl.cfg.network.adapter_name
        if cur and cur not in names:
            names.append(cur)
        for n in names:
            self.adapters.addItem(n, n)
        if cur:
            self.adapters.setCurrentIndex(max(0, names.index(cur)))
        self.adapters.blockSignals(False)

    def _adapter_chosen(self, _i: int) -> None:
        name = self.adapters.currentData()
        if name:
            self.rb_manual.setChecked(True)
            self._apply(lambda c: (setattr(c.network, "adapter_name", name), setattr(c.network, "adapter_auto", False)))

    # ------------------------------------------------------------------ Ping cíle
    COLS = ["Aktivní", "Název", "IP / doména", "Interval", "Timeout", "Opak.", "Zobrazení"]

    def _tab_targets(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._table_item_changed)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._edit_target())
        v.addWidget(self.table, 1)
        row1 = QHBoxLayout()
        for label, fn in (("Přidat", self._add_target), ("Upravit", self._edit_target), ("Smazat", self._del_target),
                          ("▲ Nahoru", lambda: self._move(-1)), ("▼ Dolů", lambda: self._move(1)),
                          ("Testovat nyní", self._test_target), ("Graf…", self._graph_target)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            row1.addWidget(b)
        v.addLayout(row1)
        row2 = QHBoxLayout()
        for label, fn in (("Export konfigurace…", self._export), ("Import konfigurace…", self._import),
                          ("Obnovit výchozí cíle", self._reset_targets)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            row2.addWidget(b)
        row2.addStretch(1)
        v.addLayout(row2)
        v.addWidget(hint("Dvojklik = úprava. Stejná IP adresa u více zařízení je povolená – záznamy se neslučují. "
                         "Interval 1–60 s, výpadek se vyhlásí po zadaném počtu po sobě jdoucích selhání."))
        return w

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        sel = self._selected_id()
        self.table.setRowCount(0)
        for t in self.ctrl.cfg.ping.targets:
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if t.enabled else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, t.id)
            self.table.setItem(r, 0, chk)
            for c, val in enumerate([t.name, t.host, f"{t.interval_s:g} s", f"{t.timeout_s:g} s", str(t.retries),
                                     "ms" if t.show_rtt else "OK"], start=1):
                it = QTableWidgetItem(val)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, c, it)
            if t.id == sel:
                self.table.selectRow(r)
        self.table.blockSignals(False)

    def _selected_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        it = self.table.item(rows[0].row(), 0)
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or self._loading:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        on = item.checkState() == Qt.CheckState.Checked

        def fn(c: AppConfig):
            t = c.target_by_id(tid)
            if t:
                t.enabled = on
        self._apply(fn)

    def _add_target(self) -> None:
        d = TargetDialog(None, self)
        if d.exec():
            t = d.result_target()
            self._apply(lambda c: c.ping.targets.append(t))
            self._fill_table()

    def _edit_target(self) -> None:
        tid = self._selected_id()
        t = self.ctrl.cfg.target_by_id(tid) if tid else None
        if not t:
            return
        d = TargetDialog(t, self)
        if d.exec():
            nt = d.result_target()
            self._apply(lambda c: c.ping.targets.__setitem__(
                next(i for i, x in enumerate(c.ping.targets) if x.id == tid), nt))
            self._fill_table()

    def _del_target(self) -> None:
        tid = self._selected_id()
        t = self.ctrl.cfg.target_by_id(tid) if tid else None
        if not t:
            return
        if QMessageBox.question(self, "Smazat cíl", f"Smazat '{t.name}' ({t.host})?\nHistorie v databázi zůstane.") \
                != QMessageBox.StandardButton.Yes:
            return
        self._apply(lambda c: setattr(c.ping, "targets", [x for x in c.ping.targets if x.id != tid]))
        self._fill_table()

    def _move(self, d: int) -> None:
        tid = self._selected_id()
        if not tid:
            return

        def fn(c: AppConfig):
            ts = c.ping.targets
            i = next(i for i, x in enumerate(ts) if x.id == tid)
            j = i + d
            if 0 <= j < len(ts):
                ts[i], ts[j] = ts[j], ts[i]
        self._apply(fn)
        self._fill_table()

    def _test_target(self) -> None:
        tid = self._selected_id()
        if tid and self.ctrl.engine.test_now(tid):
            self.status.setText("Test odeslán – výsledek se zobrazí ve widgetu.")
        elif tid:
            self.status.setText("Cíl je vypnutý – zapněte jej pro test.")

    def _graph_target(self) -> None:
        tid = self._selected_id()
        if tid:
            self.history_requested.emit(tid)

    def _export(self) -> None:
        fn, _ = QFileDialog.getSaveFileName(self, "Export konfigurace",
                                            str(Path.home() / f"netmonitor-config-{time.strftime('%Y%m%d')}.json"),
                                            "JSON (*.json)")
        if fn:
            try:
                cfgmod.export_config(self.ctrl.cfg, Path(fn))
                self.status.setText(f"Exportováno: {fn}")
            except OSError as e:
                QMessageBox.warning(self, "Export", str(e))

    def _import(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Import konfigurace", str(Path.home()), "JSON (*.json)")
        if not fn:
            return
        try:
            cfg = cfgmod.import_config(Path(fn), self.ctrl.cfg)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import", f"Soubor nelze načíst:\n{e}")
            return
        cfg.appearance.x, cfg.appearance.y = self.ctrl.cfg.appearance.x, self.ctrl.cfg.appearance.y
        self.ctrl.update_config(cfg)
        self.load()
        self.status.setText(f"Importováno: {fn}")

    def _reset_targets(self) -> None:
        if QMessageBox.question(self, "Výchozí cíle", "Nahradit seznam cílů výchozími 10 položkami?") \
                == QMessageBox.StandardButton.Yes:
            self._apply(lambda c: setattr(c.ping, "targets", default_targets()))
            self._fill_table()

    # ------------------------------------------------------------------ Upozornění
    def _tab_notifications(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(self._check("Povolit lokální upozornění", "notifications", "enabled"))
        for key, label in (("target_down", "Ztráta dostupnosti cíle"), ("target_up", "Obnovení dostupnosti cíle"),
                           ("wan_change", "Změna WAN IP"), ("internet_down", "Výpadek internetu"),
                           ("internet_up", "Obnovení internetu"), ("high_latency", "Dlouhodobě vysoká latence")):
            f.addRow(self._check(label, "notifications", key))
        f.addRow("Prodleva před upozorněním na výpadek", self._spin("notifications", "delay_s", 0, 3600, " s"))
        f.addRow("Min. odstup opakovaných upozornění", self._spin("notifications", "cooldown_s", 0, 86400, " s", 30))
        f.addRow("Vysoká latence nad", self._spin("notifications", "high_latency_ms", 1, 20000, " ms", 10, 0))
        f.addRow("… trvající alespoň", self._spin("notifications", "high_latency_duration_s", 10, 86400, " s", 10))
        f.addRow(hint("Výpadek se hlásí až po uplynutí prodlevy; krátké výpadky se tak nehlásí vůbec. "
                      "Obnovení se hlásí jen tehdy, pokud byl ohlášen výpadek."))
        return w

    # ------------------------------------------------------------------ Historie
    def _tab_history(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(self._check("Ukládat historii měření", "history", "enabled"))
        f.addRow("Doba uchování", self._spin("history", "retention_days", 1, 3650, " dní"))
        b = QPushButton("Otevřít historii a grafy…")
        b.clicked.connect(lambda: self.history_requested.emit(""))
        f.addRow(b)
        f.addRow(hint(f"Databáze: {self.ctrl.history.path}\nMěření se agregují po minutách (min/průměr/max, "
                      "počty OK/chyb) a zapisují dávkově 1× za minutu – disk se zbytečně nezatěžuje. "
                      "Události výpadků se ukládají okamžitě a odděleně."))
        return w
