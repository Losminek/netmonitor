"""Malé stavební prvky widgetu: nadpis se září, neonová linka, hodnotový label."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from . import theme


class GlowTitle(QWidget):
    """Nadpis (např. SÍŤOVÉ INFORMACE / PING) se statickou září a volitelnou linkou."""

    def __init__(self, text: str, px: int = 30, underline: str = "under", parent=None):
        super().__init__(parent)
        self._text = text
        self._px = px
        self._underline = underline       # "under" | "sides" | "none"
        self._neon = True
        self._scale = 1.0
        self._cache: QPixmap | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_height()

    def set_style(self, scale: float, neon: bool) -> None:
        if (scale, neon) != (self._scale, self._neon):
            self._scale, self._neon = scale, neon
            self._cache = None
            self._update_height()
            self.update()

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._cache = None
            self.update()

    def _font(self) -> QFont:
        f = theme.font("display", self._px, QFont.Weight.Normal, self._scale)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        return f

    def _update_height(self) -> None:
        fm = QFontMetricsF(self._font())
        extra = 18 if self._underline == "under" else 6
        self.setFixedHeight(int(fm.height() + extra * self._scale + 10 * self._scale))

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    def paintEvent(self, _):
        dpr = self.devicePixelRatioF()
        if self._cache is None or self._cache.size() != (self.size() * dpr):
            self._cache = self._render(dpr)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)

    def _render(self, dpr: float) -> QPixmap:
        pm = QPixmap(self.size() * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = self._font()
        fm = QFontMetricsF(f)
        w = self.width()
        tw = fm.horizontalAdvance(self._text)
        tx = (w - tw) / 2
        ty = 4 * self._scale + fm.ascent()
        path = QPainterPath()
        path.addText(QPointF(tx, ty), f, self._text)
        if self._neon:
            for width, alpha in ((8, 22), (5, 40), (2.6, 70)):
                p.setPen(QPen(QColor(34, 211, 255, alpha), width * self._scale, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.drawPath(path)
        # mírné "zesílení" písma tahem (Michroma má jen jednu tloušťku)
        p.setPen(QPen(QColor("#f4fbff"), 1.1 * self._scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(QColor("#f4fbff"))
        p.drawPath(path)
        s = self._scale
        if self._underline == "under":
            y = ty + fm.descent() + 10 * s
            theme.paint_neon_line(p, 8 * s, w - 8 * s, y, self._neon, thick_left=True, scale=s)
        elif self._underline == "sides":
            y = ty - fm.capHeight() / 2
            gap = 22 * s
            theme.paint_neon_line(p, 8 * s, tx - gap, y, self._neon, scale=s, fade="both")
            theme.paint_neon_line(p, tx + tw + gap, w - 8 * s, y, self._neon, scale=s, fade="both")
        p.end()
        return pm


class NeonSeparator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._neon = True
        self._scale = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(14)

    def set_style(self, scale: float, neon: bool) -> None:
        self._scale, self._neon = scale, neon
        self.setFixedHeight(max(8, int(14 * scale)))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        y = self.height() / 2
        theme.paint_neon_line(p, 2, self.width() - 2, y, self._neon, scale=self._scale)

    def sizeHint(self):
        return QSize(100, 14)


class ValueLabel(QLabel):
    """Label, který překresluje jen při skutečné změně textu/barvy/tooltipu."""

    def __init__(self, text: str = "", color: QColor | str = theme.WHITE, parent=None):
        super().__init__(text, parent)
        self._color = QColor(color)
        self._apply_color()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def _apply_color(self) -> None:
        self.setStyleSheet(f"color: {self._color.name()}; background: transparent;")

    def set_value(self, text: str, color: QColor | str | None = None, tooltip: str | None = None) -> None:
        if text != self.text():
            self.setText(text)
        if color is not None:
            c = QColor(color)
            if c != self._color:
                self._color = c
                self._apply_color()
        if tooltip is not None and tooltip != self.toolTip():
            self.setToolTip(tooltip)


def elide(text: str, font: QFont, width: float) -> str:
    return QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, width)


__all__ = ["GlowTitle", "NeonSeparator", "ValueLabel", "elide", "QRectF"]
