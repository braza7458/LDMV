"""Icônes vectorielles dessinées au QPainter (aucun fichier image requis)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 40


def _make(draw, color: str) -> QPixmap:
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(SIZE / 20, SIZE / 20)
    p.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    draw(p, QColor(color))
    p.end()
    return pm


def _icon(draw, normal="#d6d6d6", active="#22d3ee", disabled="#5a5a5a") -> QIcon:
    icon = QIcon()
    icon.addPixmap(_make(draw, normal), QIcon.Normal, QIcon.Off)
    icon.addPixmap(_make(draw, active), QIcon.Normal, QIcon.On)
    icon.addPixmap(_make(draw, active), QIcon.Active, QIcon.On)
    icon.addPixmap(_make(draw, disabled), QIcon.Disabled, QIcon.Off)
    return icon


def _cursor(p: QPainter, c: QColor):
    path = QPainterPath(QPointF(5, 3))
    for x, y in [(5, 16), (8.5, 12.5), (11, 17.5), (13, 16.5), (10.5, 11.5), (15, 11.5)]:
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)


def _split(p: QPainter, c: QColor):
    # ][ : la coupe façon CapCut
    p.drawLine(QPointF(10, 3), QPointF(10, 17))
    p.drawPolyline([QPointF(4, 5), QPointF(7, 5), QPointF(7, 15), QPointF(4, 15)])
    p.drawPolyline([QPointF(16, 5), QPointF(13, 5), QPointF(13, 15), QPointF(16, 15)])


def _scissors(p: QPainter, c: QColor):
    p.drawEllipse(QPointF(5.5, 14.5), 2.5, 2.5)
    p.drawEllipse(QPointF(14.5, 14.5), 2.5, 2.5)
    p.drawLine(QPointF(7.3, 12.7), QPointF(14, 3))
    p.drawLine(QPointF(12.7, 12.7), QPointF(6, 3))


def _trash(p: QPainter, c: QColor):
    p.drawLine(QPointF(3.5, 5.5), QPointF(16.5, 5.5))
    p.drawPolyline([QPointF(8, 5.5), QPointF(8, 3.5), QPointF(12, 3.5), QPointF(12, 5.5)])
    p.drawPolyline([QPointF(5, 5.5), QPointF(6, 17), QPointF(14, 17), QPointF(15, 5.5)])
    p.drawLine(QPointF(8.5, 8.5), QPointF(8.5, 14))
    p.drawLine(QPointF(11.5, 8.5), QPointF(11.5, 14))


def _undo(p: QPainter, c: QColor):
    path = QPainterPath(QPointF(6, 8))
    path.lineTo(13, 8)
    path.arcTo(QRectF(9, 8, 8, 8), 90, -180)
    path.lineTo(8, 16)
    p.drawPath(path)
    p.drawPolyline([QPointF(9, 4.5), QPointF(5.5, 8), QPointF(9, 11.5)])


def _redo(p: QPainter, c: QColor):
    p.translate(20, 0)
    p.scale(-1, 1)
    _undo(p, c)


def _magnet(p: QPainter, c: QColor):
    # Aimant de piste principale : deux clips recollés par un aimant
    p.fillRect(QRectF(2, 7, 7, 6), c)
    p.fillRect(QRectF(11, 7, 7, 6), c)
    p.drawLine(QPointF(10, 4), QPointF(10, 16))


def _align(p: QPainter, c: QColor):
    # Alignement automatique : deux pistes accrochées sur une même ligne
    p.drawLine(QPointF(10, 2.5), QPointF(10, 17.5))
    p.fillRect(QRectF(3, 4.5, 7, 4.5), c)
    p.fillRect(QRectF(10, 11, 7, 4.5), c)


def _silence(p: QPainter, c: QColor):
    # forme d'onde avec un trou au milieu
    for x, h in [(3, 6), (5, 10), (7, 5), (13, 7), (15, 11), (17, 5)]:
        p.drawLine(QPointF(x, 10 - h / 2), QPointF(x, 10 + h / 2))
    pen = p.pen()
    pen.setColor(QColor("#ff6b4a"))
    p.setPen(pen)
    p.drawLine(QPointF(8.5, 10), QPointF(11.5, 10))


def _zoom(sign: str):
    def draw(p: QPainter, c: QColor):
        p.drawEllipse(QPointF(10, 10), 7, 7)
        p.drawLine(QPointF(6.5, 10), QPointF(13.5, 10))
        if sign == "+":
            p.drawLine(QPointF(10, 6.5), QPointF(10, 13.5))
    return draw


def cursor_icon(): return _icon(_cursor)
def split_icon(): return _icon(_split)
def scissors_icon(): return _icon(_scissors)
def trash_icon(): return _icon(_trash)
def undo_icon(): return _icon(_undo)
def redo_icon(): return _icon(_redo)
def magnet_icon(): return _icon(_magnet)
def align_icon(): return _icon(_align)
def silence_icon(): return _icon(_silence)
def zoom_in_icon(): return _icon(_zoom("+"))
def zoom_out_icon(): return _icon(_zoom("-"))


def scissors_cursor_pixmap() -> QPixmap:
    return _make(_scissors, "#ffffff").scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
