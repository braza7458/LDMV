"""Timeline façon CapCut : règle, tête de lecture, pistes, clips avec forme d'onde.

Interactions (outil Sélection) :
  - clic sur la règle / glisser      -> déplace la tête de lecture
  - clic sur un clip                 -> sélection (Ctrl/Maj : multiple)
  - glisser un clip                  -> déplacement (aimant + accrochage)
  - survol de la jonction entre deux clips adjacents -> curseur ⇔,
    glisser = ROLL EDIT (Partie 3)
  - survol d'un bord libre de clip   -> curseur ↔, glisser = rognage
Outil Ciseau (touche B) : clic sur un clip = coupe à cet endroit.
Mode Silences : une barre orange horizontale sur la piste principale ;
  la faire glisser règle le seuil, tout ce qui reste dessous sera coupé.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ldmv.core.edits import Editor
from ldmv.core.model import Clip, Track
from ldmv.core.silence import Interval, db_to_display, display_to_db
from ldmv.core.timecode import US_PER_SECOND, format_ruler, format_timecode
from ldmv.ui.icons import scissors_cursor_pixmap

LEFT_PAD = 16
RULER_H = 30
TOP_GAP = 60
TRACK_H = 96
TRACK_GAP = 10
CLIP_HEADER_H = 18
EDGE_PX = 6
SNAP_PX = 8

BG = QColor("#262626")
RULER_TEXT = QColor("#8a8a8a")
CLIP_BG = QColor("#0c5a60")
CLIP_BG_SEL = QColor("#11707a")
WAVE = QColor("#1fb3bf")
WAVE_QUIET = QColor("#2b6f75")
LABEL_BG = QColor(0, 0, 0, 90)
PLAYHEAD = QColor("#f2f2f2")
SNAP_LINE = QColor("#ffd23f")
SILENCE = QColor(255, 80, 60, 110)
SPLIT_LINE = QColor("#ff5a4a")
THRESHOLD = QColor("#ffa62b")
THRESHOLD_GRAB_PX = 6
THRESHOLD_RANGE = (-70.0, -5.0)


@dataclass
class Hit:
    kind: str                       # ruler | junction | edge_left | edge_right | clip | empty
    track: Track | None = None
    clip: Clip | None = None
    right: Clip | None = None       # pour une jonction


class TimelineWidget(QWidget):
    changed = Signal()              # le contenu de la timeline a changé
    playheadChanged = Signal(int)
    selectionChanged = Signal()
    toolChanged = Signal(str)
    thresholdDragged = Signal(float)  # nouveau seuil (dB) choisi à la souris

    def __init__(self, editor: Editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.pps = 60.0             # pixels par seconde (zoom)
        self.tool = "select"        # select | split
        self.preview: dict[str, list[Interval]] = {}  # silences à mettre en évidence
        self.threshold_db: float | None = None  # barre de seuil (None = masquée)
        self._drag: dict | None = None
        self._hover_x: float | None = None
        self._snap_x: float | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._scissors_cursor = QCursor(scissors_cursor_pixmap(), 6, 4)
        self.update_size()

    # -- géométrie -----------------------------------------------------------
    @property
    def timeline(self):
        return self.editor.timeline

    def x_of(self, t: int) -> float:
        return LEFT_PAD + t / US_PER_SECOND * self.pps

    def t_of(self, x: float) -> int:
        return round((x - LEFT_PAD) / self.pps * US_PER_SECOND)

    def px_to_us(self, px: float) -> int:
        return round(px / self.pps * US_PER_SECOND)

    def track_rect(self, index: int) -> QRectF:
        y = RULER_H + TOP_GAP + index * (TRACK_H + TRACK_GAP)
        return QRectF(0, y, max(self.width(), 1), TRACK_H)

    def clip_rect(self, index: int, clip: Clip) -> QRectF:
        row = self.track_rect(index)
        x0, x1 = self.x_of(clip.start), self.x_of(clip.end)
        return QRectF(x0, row.top(), max(1.0, x1 - x0), TRACK_H)

    @staticmethod
    def wave_area(row: QRectF) -> tuple[float, float]:
        """(bas, hauteur) de la zone de forme d'onde dans une ligne de piste."""
        top = row.top() + CLIP_HEADER_H + 2
        bottom = row.bottom() - 2
        return bottom, bottom - top

    def threshold_y(self) -> float | None:
        if self.threshold_db is None:
            return None
        base, height = self.wave_area(self.track_rect(self._main_index()))
        return base - float(db_to_display(self.threshold_db)) * height

    def _main_index(self) -> int:
        return self.timeline.tracks.index(self.timeline.main_track)

    def set_threshold_line(self, db: float | None) -> None:
        self.threshold_db = db
        self.update()

    def update_size(self) -> None:
        width = int(self.x_of(self.timeline.duration) + 400)
        height = int(self.track_rect(len(self.timeline.tracks)).top() + 20)
        self.setMinimumSize(width, height)
        self.update()

    def set_zoom(self, pps: float, anchor_t: int | None = None) -> None:
        self.pps = max(0.5, min(2000.0, pps))
        self.update_size()

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self._update_cursor(None)
        self.toolChanged.emit(tool)
        self.update()

    # -- rendu ---------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(event.rect(), BG)
        visible = QRectF(event.rect())
        self._paint_ruler(p, visible)
        for i, track in enumerate(self.timeline.tracks):
            for clip in track.clips:
                rect = self.clip_rect(i, clip)
                if rect.right() >= visible.left() and rect.left() <= visible.right():
                    self._paint_clip(p, rect, clip, visible, track is self.timeline.main_track)
        self._paint_threshold(p, visible)
        self._paint_overlays(p)
        p.end()

    def _paint_threshold(self, p: QPainter, visible: QRectF) -> None:
        y = self.threshold_y()
        if y is None:
            return
        track = self.timeline.main_track
        x0 = max(self.x_of(0), visible.left())
        x1 = min(self.x_of(track.end), visible.right())
        if x1 > x0:
            p.setPen(QPen(THRESHOLD, 2))
            p.drawLine(QLineF(x0, y, x1, y))
        # Étiquette accrochée au bord gauche visible, comme une poignée
        text = f"Seuil {self.threshold_db:.1f} dB"
        p.setFont(QFont(self.font().family(), 8, QFont.Bold))
        tw = p.fontMetrics().horizontalAdvance(text) + 14
        lx = max(self.x_of(0), visible.left()) + 4
        row = self.track_rect(self._main_index())
        ly = row.top() - 20
        p.setBrush(THRESHOLD)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(lx, ly, tw, 17), 4, 4)
        p.setBrush(Qt.NoBrush)
        p.setPen(QColor("#1a1a1a"))
        p.drawText(QRectF(lx, ly, tw, 17), Qt.AlignCenter, text)

    def _ruler_step(self) -> tuple[float, int]:
        """Pas des graduations : (secondes entre libellés, sous-divisions)."""
        for step in (0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800):
            if step * self.pps >= 90:
                return step, 10 if step >= 1 else 5
        return 3600, 6

    def _paint_ruler(self, p: QPainter, visible: QRectF) -> None:
        step, sub = self._ruler_step()
        p.setFont(QFont(self.font().family(), 8))
        t0 = max(0.0, (visible.left() - LEFT_PAD) / self.pps)
        first = int(t0 // step) * step
        t = first
        end = (visible.right() - LEFT_PAD) / self.pps
        while t <= end + step:
            x = LEFT_PAD + t * self.pps
            p.setPen(QPen(RULER_TEXT, 1))
            p.drawLine(QLineF(x, 4, x, RULER_H - 6))
            label = format_ruler(round(t * US_PER_SECOND)) if step >= 1 else f"{t:.1f}s"
            p.drawText(QPointF(x + 4, 15), label)
            for k in range(1, sub):
                xs = x + k * step / sub * self.pps
                p.drawLine(QLineF(xs, RULER_H - 11, xs, RULER_H - 6))
            t += step

    def _paint_clip(self, p: QPainter, rect: QRectF, clip: Clip, visible: QRectF, on_main: bool) -> None:
        selected = clip.id in self.editor.selection
        p.fillRect(rect, CLIP_BG_SEL if selected else CLIP_BG)
        source = self.timeline.sources.get(clip.source_id)

        # Forme d'onde : barres depuis le bas, uniquement sur la partie visible.
        base, wave_h = self.wave_area(rect)
        wave_top = base - wave_h
        x0 = max(rect.left(), visible.left())
        x1 = min(rect.right(), visible.right())
        if source is not None and source.peaks is not None and x1 - x0 >= 1:
            peaks: np.ndarray = source.peaks
            n = int(x1 - x0)
            src = clip.source_in + (np.arange(n + 1) + (x0 - rect.left())) / self.pps * US_PER_SECOND
            idx = np.clip((src * source.peaks_rate / US_PER_SECOND).astype(np.int64), 0, len(peaks) - 1)
            # max des pics couverts par chaque pixel (1 pic si zoom fort)
            values = np.maximum.reduceat(peaks[: idx[-1] + 1], idx[:-1]) if len(peaks) else np.zeros(n)
            # Sous la barre de seuil, les barres sont estompées : c'est ce qui sera coupé.
            limit = float(db_to_display(self.threshold_db)) if on_main and self.threshold_db is not None else -1
            loud, quiet = [], []
            for k, v in enumerate(values):
                if v > 0.01:
                    line = QLineF(x0 + k + 0.5, base, x0 + k + 0.5, base - v * wave_h)
                    (loud if v >= limit else quiet).append(line)
            p.setPen(QPen(WAVE, 1))
            p.drawLines(loud)
            if quiet:
                p.setPen(QPen(WAVE_QUIET, 1))
                p.drawLines(quiet)
            p.setPen(QPen(QColor(255, 255, 255, 50), 1))
            mid = wave_top + wave_h * 0.55
            p.drawLine(QLineF(x0, mid, x1, mid))

        # Silences détectés (aperçu de la Partie 1).
        holes = self.preview.get(clip.source_id)
        if holes:
            for a, b in holes:
                if b <= clip.source_in or a >= clip.source_out:
                    continue
                ta = clip.timeline_time(max(a, clip.source_in))
                tb = clip.timeline_time(min(b, clip.source_out))
                p.fillRect(QRectF(self.x_of(ta), rect.top(), self.x_of(tb) - self.x_of(ta), rect.height()), SILENCE)

        # En-tête : nom + durée, comme dans CapCut.
        if rect.width() > 40:
            p.save()
            p.setClipRect(rect)
            p.setFont(QFont(self.font().family(), 8))
            name = source.name if source else clip.source_id
            text = f"{name}   {format_timecode(clip.duration, self.timeline.fps)}"
            tw = p.fontMetrics().horizontalAdvance(text) + 10
            p.fillRect(QRectF(rect.left() + 3, rect.top() + 3, tw, CLIP_HEADER_H - 4), LABEL_BG)
            p.setPen(QColor("#e8e8e8"))
            p.drawText(QPointF(rect.left() + 8, rect.top() + CLIP_HEADER_H - 5), text)
            p.restore()

        p.setPen(QPen(QColor("#ffffff") if selected else QColor(0, 0, 0, 120), 2 if selected else 1))
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

    def _paint_overlays(self, p: QPainter) -> None:
        h = self.height()
        if self._snap_x is not None:
            p.setPen(QPen(SNAP_LINE, 1, Qt.DashLine))
            p.drawLine(QLineF(self._snap_x, RULER_H, self._snap_x, h))
        if self.tool == "split" and self._hover_x is not None:
            p.setPen(QPen(SPLIT_LINE, 1))
            p.drawLine(QLineF(self._hover_x, RULER_H, self._hover_x, h))
        # Tête de lecture
        x = self.x_of(self.timeline.playhead)
        p.setPen(QPen(PLAYHEAD, 1.2))
        p.drawLine(QLineF(x, 2, x, h))
        p.setBrush(PLAYHEAD)
        p.drawRoundedRect(QRectF(x - 5, 1, 10, 13), 3, 3)
        p.setBrush(Qt.NoBrush)
        # Indication pendant un roll edit
        if self._drag and self._drag["kind"] == "junction":
            delta = self._drag.get("applied", 0)
            frames = round(delta * self.timeline.fps / US_PER_SECOND)
            jx = self.x_of(self._drag["junction"] + delta)
            text = f"Roll {frames:+d} img"
            p.setFont(QFont(self.font().family(), 8, QFont.Bold))
            tw = p.fontMetrics().horizontalAdvance(text) + 12
            y = self._drag["row_top"] - 22
            p.fillRect(QRectF(jx - tw / 2, y, tw, 18), QColor(0, 0, 0, 180))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(jx - tw / 2, y, tw, 18), Qt.AlignCenter, text)

    # -- hit-test --------------------------------------------------------------
    def hit(self, pos: QPointF) -> Hit:
        if pos.y() < RULER_H + TOP_GAP / 2:
            return Hit("ruler")
        y = self.threshold_y()
        if y is not None and abs(pos.y() - y) <= THRESHOLD_GRAB_PX \
                and self.x_of(0) <= pos.x() <= self.x_of(self.timeline.main_track.end):
            return Hit("threshold", self.timeline.main_track)
        for i, track in enumerate(self.timeline.tracks):
            row = self.track_rect(i)
            if not (row.top() <= pos.y() <= row.bottom()):
                continue
            track.sort()
            x = pos.x()
            for left, right in zip(track.clips, track.clips[1:]):
                if left.end == right.start and abs(self.x_of(left.end) - x) <= EDGE_PX:
                    return Hit("junction", track, left, right)
            for clip in track.clips:
                x0, x1 = self.x_of(clip.start), self.x_of(clip.end)
                if x1 - x0 > 3 * EDGE_PX:
                    if abs(x - x0) <= EDGE_PX:
                        return Hit("edge_left", track, clip)
                    if abs(x - x1) <= EDGE_PX:
                        return Hit("edge_right", track, clip)
                if x0 <= x <= x1:
                    return Hit("clip", track, clip)
            return Hit("empty", track)
        return Hit("empty")

    def _update_cursor(self, hit: Hit | None) -> None:
        if self.tool == "split":
            self.setCursor(self._scissors_cursor)
        elif hit and hit.kind == "threshold":
            self.setCursor(Qt.SizeVerCursor)
        elif hit and hit.kind == "junction":
            self.setCursor(Qt.SplitHCursor)       # curseur spécial du roll edit
        elif hit and hit.kind in ("edge_left", "edge_right"):
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # -- accrochage --------------------------------------------------------------
    def _snap(self, t: int, exclude: set[str] = frozenset(), with_playhead: bool = True) -> int:
        """Alignement automatique (Partie 2) : accroche aux bords de clips et à la
        tête de lecture, sur toutes les pistes."""
        self._snap_x = None
        if not self.timeline.auto_align:
            return t
        threshold = self.px_to_us(SNAP_PX)
        candidates = [0]
        if with_playhead:
            candidates.append(self.timeline.playhead)
        for _, clip in self.timeline.all_clips():
            if clip.id not in exclude:
                candidates += (clip.start, clip.end)
        best = min(candidates, key=lambda c: abs(c - t))
        if abs(best - t) <= threshold:
            self._snap_x = self.x_of(best)
            return best
        return t

    # -- souris ------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        hit = self.hit(pos)
        t = self.t_of(pos.x())

        if hit.kind == "ruler":
            self._set_playhead(self._snap(max(0, t), with_playhead=False))
            self._drag = {"kind": "playhead"}
            return

        if hit.kind == "threshold":
            self._drag = {"kind": "threshold"}
            self._drag_threshold(pos.y())
            return

        if self.tool == "split":
            if hit.clip is not None:
                cut = self._snap(t, with_playhead=True)
                if hit.kind == "junction":
                    return
                if self.editor.split(hit.clip.id, cut):
                    self.changed.emit()
            return

        if hit.kind == "empty" or hit.clip is None:
            if self.editor.selection:
                self.editor.selection.clear()
                self.selectionChanged.emit()
            self._set_playhead(max(0, t))
            self._drag = {"kind": "playhead"}
            self.update()
            return

        mods = e.modifiers()
        if hit.kind == "clip":
            if mods & (Qt.ControlModifier | Qt.ShiftModifier):
                self.editor.selection ^= {hit.clip.id}
            elif hit.clip.id not in self.editor.selection:
                self.editor.selection = {hit.clip.id}
            self.selectionChanged.emit()

        self.editor.begin_gesture()
        self._drag = {
            "kind": hit.kind,
            "press_t": t,
            "clip": hit.clip.id,
            "right": hit.right.id if hit.right else None,
            "orig_start": hit.clip.start,
            "orig_end": hit.clip.end,
            "junction": hit.clip.end,
            "row_top": self.track_rect(self.timeline.tracks.index(hit.track)).top(),
            "applied": 0,
            "moved": False,
        }
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        t = self.t_of(pos.x())
        self._hover_x = pos.x()
        d = self._drag
        if d is None:
            hit = self.hit(pos)
            self._update_cursor(hit)
            if self.tool == "split":
                if hit.clip is not None:
                    self._hover_x = self.x_of(self._snap(t))
                    self._snap_x = None
                else:
                    self._hover_x = None
            self.update()
            return

        if d["kind"] == "playhead":
            self._set_playhead(self._snap(max(0, t), with_playhead=False))
            return
        if d["kind"] == "threshold":
            self._drag_threshold(pos.y())
            return

        delta = t - d["press_t"]
        if abs(delta) < self.px_to_us(2) and not d["moved"]:
            return
        d["moved"] = True
        ed = self.editor
        ed.reset_gesture()
        if d["kind"] == "junction":
            # ROLL EDIT : la jonction suit la souris, durée totale constante.
            target = self._snap(d["junction"] + delta, exclude={d["clip"], d["right"]})
            d["applied"] = ed.roll(d["clip"], d["right"], target - d["junction"])
        elif d["kind"] == "edge_left":
            ed.trim(d["clip"], "left", self._snap(d["orig_start"] + delta, exclude={d["clip"]}))
        elif d["kind"] == "edge_right":
            ed.trim(d["clip"], "right", self._snap(d["orig_end"] + delta, exclude={d["clip"]}))
        elif d["kind"] == "clip":
            new_start = d["orig_start"] + delta
            dur = d["orig_end"] - d["orig_start"]
            s1 = self._snap(new_start, exclude={d["clip"]})
            snap_x_start = self._snap_x
            s2 = self._snap(new_start + dur, exclude={d["clip"]}) - dur
            if abs(s1 - new_start) <= abs(s2 - new_start) and s1 != new_start:
                new_start, self._snap_x = s1, snap_x_start
            elif s2 != new_start:
                new_start = s2
            ed.move(d["clip"], new_start)
        self.update_size()
        self.changed.emit()

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        self._snap_x = None
        if d and d["kind"] not in ("playhead", "threshold"):
            self.editor.end_gesture()
            self.changed.emit()
        self.update()

    def leaveEvent(self, e):
        self._hover_x = None
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.set_zoom(self.pps * factor)
            e.accept()
        else:
            super().wheelEvent(e)

    def _drag_threshold(self, y: float) -> None:
        base, height = self.wave_area(self.track_rect(self._main_index()))
        db = display_to_db((base - y) / height)
        db = round(max(THRESHOLD_RANGE[0], min(THRESHOLD_RANGE[1], db)) * 2) / 2
        self.threshold_db = db
        self.thresholdDragged.emit(db)
        self.update()

    def _set_playhead(self, t: int) -> None:
        self.timeline.playhead = t
        self.playheadChanged.emit(t)
        self.update()
