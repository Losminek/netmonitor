"""Vlastní linkové ikony kreslené QPainterem (ostré na HiDPI, bez externích souborů).

Žádné značkové logo – Google DNS používá generickou ikonu DNS.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from ..core.config import PingTarget
from ..utils.validation import host_kind, is_private_address


def auto_icon(t: PingTarget) -> str:
    if t.icon != "auto":
        return t.icon
    if host_kind(t.host) == "domain":
        return "globe"
    if is_private_address(t.host):
        return "router"
    return "server"


def draw_icon(p: QPainter, kind: str, r: QRectF, color: QColor, width: float = 1.6) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = min(r.width(), r.height())
    c = r.center()
    x, y = c.x() - s / 2, c.y() - s / 2

    def P(fx, fy):
        return QPointF(x + fx * s, y + fy * s)

    if kind == "globe":
        p.drawEllipse(c, s * 0.42, s * 0.42)
        p.drawEllipse(c, s * 0.18, s * 0.42)
        p.drawLine(P(0.08, 0.5), P(0.92, 0.5))
        path = QPainterPath()
        path.moveTo(P(0.17, 0.28))
        path.quadTo(P(0.5, 0.36), P(0.83, 0.28))
        path.moveTo(P(0.17, 0.72))
        path.quadTo(P(0.5, 0.64), P(0.83, 0.72))
        p.drawPath(path)
    elif kind == "dns":   # databáze
        rx, ry = s * 0.32, s * 0.11
        for fy in (0.24,):
            p.drawEllipse(QPointF(c.x(), y + fy * s), rx, ry)
        for fy in (0.5, 0.76):
            path = QPainterPath()
            path.moveTo(QPointF(c.x() - rx, y + fy * s - ry * 0.0))
            path.arcTo(QRectF(c.x() - rx, y + fy * s - ry, 2 * rx, 2 * ry), 180, 180)
            p.drawPath(path)
        p.drawLine(QPointF(c.x() - rx, y + 0.24 * s), QPointF(c.x() - rx, y + 0.76 * s))
        p.drawLine(QPointF(c.x() + rx, y + 0.24 * s), QPointF(c.x() + rx, y + 0.76 * s))
    elif kind == "server":
        for fy in (0.16, 0.54):
            p.drawRoundedRect(QRectF(P(0.12, fy), P(0.88, fy + 0.3)), s * 0.05, s * 0.05)
            p.drawPoint(P(0.25, fy + 0.15))
            p.drawLine(P(0.55, fy + 0.15), P(0.75, fy + 0.15))
    elif kind == "wifi":
        for k, rad in enumerate((0.42, 0.29, 0.16)):
            rect = QRectF(c.x() - rad * s, y + 0.78 * s - rad * s, 2 * rad * s, 2 * rad * s)
            p.drawArc(rect, 45 * 16, 90 * 16)
        p.setBrush(color)
        p.drawEllipse(QPointF(c.x(), y + 0.76 * s), s * 0.045, s * 0.045)
    elif kind == "router":
        p.drawRoundedRect(QRectF(P(0.1, 0.55), P(0.9, 0.82)), s * 0.05, s * 0.05)
        p.drawLine(P(0.25, 0.55), P(0.25, 0.18))
        p.drawLine(P(0.75, 0.55), P(0.75, 0.18))
        p.setPen(QPen(QColor("#3dff6e"), width * 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for fx in (0.24, 0.36, 0.48):
            p.drawPoint(P(fx, 0.685))
    elif kind == "modem":
        p.drawRoundedRect(QRectF(P(0.08, 0.36), P(0.92, 0.64)), s * 0.1, s * 0.1)
        for fx in (0.25, 0.38, 0.51, 0.64):
            p.drawPoint(P(fx, 0.5))
    elif kind == "box":   # krychle
        top = [P(0.5, 0.1), P(0.88, 0.3), P(0.5, 0.5), P(0.12, 0.3)]
        path = QPainterPath()
        path.moveTo(top[0])
        for pt in top[1:] + [top[0]]:
            path.lineTo(pt)
        path.moveTo(P(0.12, 0.3))
        path.lineTo(P(0.12, 0.72))
        path.lineTo(P(0.5, 0.92))
        path.lineTo(P(0.88, 0.72))
        path.lineTo(P(0.88, 0.3))
        path.moveTo(P(0.5, 0.5))
        path.lineTo(P(0.5, 0.92))
        p.drawPath(path)
    elif kind == "cloud":
        path = QPainterPath()
        path.moveTo(P(0.25, 0.75))
        path.arcTo(QRectF(P(0.06, 0.42), P(0.42, 0.78)), 270, -180)
        path.arcTo(QRectF(P(0.26, 0.22), P(0.7, 0.66)), 160, -150)
        path.arcTo(QRectF(P(0.6, 0.42), P(0.94, 0.76)), 90, -180)
        path.closeSubpath()
        p.drawPath(path)
    else:
        p.drawEllipse(c, s * 0.3, s * 0.3)
    p.restore()


@lru_cache(maxsize=8)
def app_icon_pixmap(size: int = 256, state: str = "ok") -> QPixmap:
    """Ikona aplikace / tray: tmavý zaoblený čtverec, azurový globus a stavová tečka."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    p.setPen(QPen(QColor(34, 211, 255), max(1.5, size * 0.035)))
    p.setBrush(QColor(8, 30, 58))
    p.drawRoundedRect(r, size * 0.2, size * 0.2)
    draw_icon(p, "globe", r.adjusted(size * 0.16, size * 0.16, -size * 0.16, -size * 0.16), QColor(70, 225, 255),
              max(1.4, size * 0.05))
    col = {"ok": "#3dff6e", "warn": "#ffaa2b", "bad": "#ff4d5e"}.get(state, "#8a9bb0")
    d = size * 0.3
    p.setPen(QPen(QColor(8, 30, 58), max(1.0, size * 0.04)))
    p.setBrush(QColor(col))
    p.drawEllipse(QRectF(size - d - size * 0.02, size - d - size * 0.02, d, d))
    p.end()
    return pm


def app_icon(state: str = "ok") -> QIcon:
    ic = QIcon()
    for sz in (16, 20, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(app_icon_pixmap(sz, state))
    return ic
