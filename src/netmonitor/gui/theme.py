"""Vizuální styl NetMonitoru: barvy, písma, QSS, kreslení neonových prvků.

Neonové efekty se kreslí staticky (žádné animace, žádné časovače) – CPU/GPU
se zatěžuje jen při skutečném překreslení.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPainterPath, QPen

from ..utils.paths import resources_dir

log = logging.getLogger(__name__)

CYAN = QColor("#22d3ff")
CYAN_SOFT = QColor(34, 211, 255, 110)
CYAN_TEXT = QColor("#38d9ff")
WHITE = QColor("#eaf6ff")
LABEL = QColor("#dbeeff")
MUTED = QColor("#8a9bb0")
GLASS_TOP = QColor(10, 42, 78)
GLASS_BOTTOM = QColor(4, 20, 44)
ROW_BG = QColor(120, 190, 255, 22)
ROW_BG_HOVER = QColor(34, 211, 255, 40)

DISPLAY_FAMILY = "Michroma"
BODY_FAMILY = "Exo 2"


@lru_cache(maxsize=1)
def load_fonts() -> tuple[str, str]:
    """Načte přibalená OFL písma; při selhání použije systémová."""
    disp, body = "Segoe UI", "Segoe UI"
    fdir = resources_dir() / "fonts"
    for fname, attr in (("Michroma.ttf", "disp"), ("Exo2.ttf", "body")):
        fid = QFontDatabase.addApplicationFont(str(fdir / fname))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fams:
            if attr == "disp":
                disp = fams[0]
            else:
                body = fams[0]
        else:
            log.warning("Písmo %s se nepodařilo načíst", fname)
    return disp, body


def font(kind: str = "body", px: float = 16, weight: QFont.Weight = QFont.Weight.Medium, scale: float = 1.0) -> QFont:
    disp, body = load_fonts()
    f = QFont(disp if kind == "display" else body)
    f.setPixelSize(max(6, round(px * scale)))
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def panel_path(r: QRectF, radius: float, notch: bool = True) -> QPainterPath:
    """Obrys skleněného panelu se zvýšeným 'tabem' vpravo nahoře (dle návrhu)."""
    p = QPainterPath()
    tab_h = 14.0 if notch else 0.0
    tab_w = min(140.0, r.width() * 0.24)
    top = r.top() + tab_h
    x0, y0, x1, y1 = r.left(), top, r.right(), r.bottom()
    p.moveTo(x0 + radius, y0)
    if notch:
        tx = x1 - tab_w
        p.lineTo(tx - tab_h, y0)
        p.lineTo(tx, r.top())
        p.lineTo(x1 - radius, r.top())
        p.quadTo(x1, r.top(), x1, r.top() + radius)
    else:
        p.lineTo(x1 - radius, y0)
        p.quadTo(x1, y0, x1, y0 + radius)
    p.lineTo(x1, y1 - radius)
    p.quadTo(x1, y1, x1 - radius, y1)
    p.lineTo(x0 + radius, y1)
    p.quadTo(x0, y1, x0, y1 - radius)
    p.lineTo(x0, y0 + radius)
    p.quadTo(x0, y0, x0 + radius, y0)
    p.closeSubpath()
    return p


def paint_glass_panel(p: QPainter, rect: QRectF, glass_alpha: float, neon: bool, scale: float = 1.0) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = 22 * scale
    outer = rect.adjusted(6, 6, -6, -6)
    path = panel_path(outer, radius)

    # sklo
    g = QLinearGradient(outer.topLeft(), outer.bottomRight())
    ga = max(0.0, min(1.0, glass_alpha))
    a = int(round(255 * ga))
    c1, c2, c3 = QColor(GLASS_TOP), QColor(8, 34, 66), QColor(GLASS_BOTTOM)
    # při 100 % je pozadí zcela neprůhledné (žádná prosvítající místa)
    c1.setAlpha(a)
    c2.setAlpha(a if ga >= 0.999 else int(a * 0.92))
    c3.setAlpha(a if ga >= 0.999 else min(255, int(a * 1.05)))
    g.setColorAt(0, c1)
    g.setColorAt(0.55, c2)
    g.setColorAt(1, c3)
    p.fillPath(path, g)

    # jemný lesk v horní části skla
    sheen = QLinearGradient(outer.topLeft(), QPointF(outer.left(), outer.top() + outer.height() * 0.35))
    sheen.setColorAt(0, QColor(140, 220, 255, int(min(40, 26 * glass_alpha / 0.55)) if glass_alpha else 0))
    sheen.setColorAt(1, QColor(140, 220, 255, 0))
    p.fillPath(path, sheen)

    # neonová záře (statická – několik průhledných tahů)
    if neon:
        for w, al in ((9, 18), (5, 34), (3, 60)):
            p.setPen(QPen(QColor(34, 211, 255, al), w * scale))
            p.drawPath(path)
    p.setPen(QPen(QColor(64, 220, 255, 230), 1.6 * scale))
    p.drawPath(path)

    # vnitřní linka (zkosený roh vpravo dole jako v návrhu)
    inner = outer.adjusted(10 * scale, 24 * scale, -10 * scale, -10 * scale)
    ip = QPainterPath()
    ch = 22 * scale
    ip.moveTo(inner.right(), inner.top() + 30 * scale)
    ip.lineTo(inner.right(), inner.bottom() - ch)
    ip.lineTo(inner.right() - ch, inner.bottom())
    ip.lineTo(inner.right() - inner.width() * 0.35, inner.bottom())
    p.setPen(QPen(QColor(34, 211, 255, 70), 1.0 * scale))
    p.drawPath(ip)

    # "///" proužky na tabu vpravo nahoře
    tab_w = min(140.0, outer.width() * 0.24)
    sx = outer.right() - tab_w + 18 * scale
    sy = outer.top() + 3 * scale
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(34, 211, 255, 235))
    for i in range(3):
        x = sx + i * 13 * scale
        stripe = QPainterPath()
        stripe.moveTo(x + 5 * scale, sy)
        stripe.lineTo(x + 12 * scale, sy)
        stripe.lineTo(x + 7 * scale, sy + 8 * scale)
        stripe.lineTo(x, sy + 8 * scale)
        stripe.closeSubpath()
        p.drawPath(stripe)

    # záblesky světla na horní a dolní hraně
    if neon:
        for y, cx in ((outer.top() + 14, outer.center().x() - outer.width() * 0.1),
                      (outer.bottom(), outer.center().x() + outer.width() * 0.15)):
            lg = QLinearGradient(cx - 90 * scale, y, cx + 90 * scale, y)
            lg.setColorAt(0, QColor(150, 240, 255, 0))
            lg.setColorAt(0.5, QColor(190, 248, 255, 220))
            lg.setColorAt(1, QColor(150, 240, 255, 0))
            p.setPen(QPen(lg, 2.2 * scale))
            p.drawLine(QPointF(cx - 90 * scale, y), QPointF(cx + 90 * scale, y))


def paint_neon_line(p: QPainter, x1: float, x2: float, y: float, neon: bool, thick_left: bool = False,
                    scale: float = 1.0, fade: str = "none") -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(x1, y, x2, y)
    if fade == "both":
        g.setColorAt(0, QColor(34, 211, 255, 0))
        g.setColorAt(0.5, QColor(64, 220, 255, 255))
        g.setColorAt(1, QColor(34, 211, 255, 0))
    elif fade == "left":
        g.setColorAt(0, QColor(34, 211, 255, 0))
        g.setColorAt(1, QColor(64, 220, 255, 255))
    elif fade == "right":
        g.setColorAt(0, QColor(64, 220, 255, 255))
        g.setColorAt(1, QColor(34, 211, 255, 0))
    else:
        g.setColorAt(0, QColor(40, 200, 255, 200))
        g.setColorAt(0.5, QColor(64, 224, 255, 255))
        g.setColorAt(1, QColor(40, 200, 255, 200))
    if neon:
        p.setPen(QPen(QColor(34, 211, 255, 40), 6 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(x1 + 4, y), QPointF(x2 - 4, y))
    p.setPen(QPen(g, 1.8 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(x1, y), QPointF(x2, y))
    if thick_left:
        tl = QPainterPath()
        w = (x2 - x1) * 0.17
        tl.moveTo(x1, y - 1 * scale)
        tl.lineTo(x1 + w, y - 3.5 * scale)
        tl.lineTo(x1 + w + 10 * scale, y)
        tl.lineTo(x1, y + 2.5 * scale)
        tl.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(70, 225, 255, 255))
        p.drawPath(tl)


def status_color(colors, kind: str) -> QColor:
    return QColor({"ok": colors.ok, "warn": colors.warn, "bad": colors.bad}.get(kind, colors.unknown))


def rtt_kind(rtt: float | None, thresholds) -> str:
    if rtt is None:
        return "unknown"
    if rtt <= thresholds.green_max_ms:
        return "ok"
    if rtt <= thresholds.orange_max_ms:
        return "warn"
    return "bad"


DIALOG_QSS = """
QDialog, QWidget#dlgRoot { background: #0b1d33; color: #dbeeff; }
QTabWidget::pane { border: 1px solid #1f4d73; border-radius: 6px; top: -1px; }
QTabBar::tab { background: #0e2742; color: #9fc7e6; padding: 7px 14px; border: 1px solid #1f4d73;
               border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
QTabBar::tab:selected { background: #13375c; color: #ffffff; border-color: #22d3ff; }
QGroupBox { border: 1px solid #1f4d73; border-radius: 6px; margin-top: 14px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #38d9ff; }
QLabel { color: #dbeeff; }
QLabel[hint="true"] { color: #8fb3d1; }
QLabel[error="true"] { color: #ff6b78; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTextBrowser {
    background: #091628; color: #eaf6ff; border: 1px solid #24557f; border-radius: 4px; padding: 4px 6px;
    selection-background-color: #1a6fa8; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #22d3ff; }
QLineEdit[invalid="true"] { border-color: #ff4d5e; }
QPushButton { background: #12365a; color: #eaf6ff; border: 1px solid #24689a; border-radius: 5px; padding: 6px 12px; }
QPushButton:hover { background: #18466f; border-color: #22d3ff; }
QPushButton:pressed { background: #0d2a47; }
QPushButton:disabled { color: #5d7a93; border-color: #1c3a55; }
QPushButton[accent="true"] { background: #0f6f8f; border-color: #22d3ff; }
QPushButton:checked { background: #0f6f8f; border-color: #22d3ff; }
QCheckBox, QRadioButton { color: #dbeeff; spacing: 8px; }
QTableWidget, QTableView, QListWidget, QTreeWidget, QTreeView { background: #081426; alternate-background-color: #0c1d35; color: #eaf6ff;
    gridline-color: #173a5a; border: 1px solid #1f4d73; border-radius: 4px; selection-background-color: #155a88; }
QTreeView::item { padding: 3px 2px; }
QTreeView::item:selected { background: #155a88; color: #ffffff; }
QTreeView::item:hover { background: #10304f; }
QRadioButton::indicator { width: 12px; height: 12px; border-radius: 8px; border: 2px solid #3a7bb0; background: #091628; }
QRadioButton::indicator:checked { border-color: #22d3ff;
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                stop:0 #22d3ff, stop:0.45 #22d3ff, stop:0.55 #091628, stop:1 #091628); }
QRadioButton::indicator:disabled { border-color: #1c3a55; }
QHeaderView::section { background: #10304f; color: #9fd8ff; border: none; border-right: 1px solid #1a4468;
    padding: 5px; }
QProgressBar { background: #091628; border: 1px solid #24557f; border-radius: 4px; text-align: center; color: #eaf6ff; }
QProgressBar::chunk { background: #22d3ff; border-radius: 3px; }
QSlider::groove:horizontal { height: 4px; background: #1d4467; border-radius: 2px; }
QSlider::handle:horizontal { background: #22d3ff; width: 14px; margin: -6px 0; border-radius: 7px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #24557f; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QToolTip { background: #0b1d33; color: #eaf6ff; border: 1px solid #22d3ff; padding: 6px; }
QMenu { background: #0b1d33; color: #eaf6ff; border: 1px solid #22d3ff; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 22px; border-radius: 3px; }
QMenu::item:selected { background: #155a88; }
QMenu::item:disabled { color: #6a86a0; }
QMenu::separator { height: 1px; background: #1f4d73; margin: 4px 8px; }
"""
