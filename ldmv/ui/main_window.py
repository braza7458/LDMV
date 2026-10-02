"""Fenêtre principale : médiathèque, aperçu, barre d'outils et timeline."""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QScrollArea,
                               QSlider, QSplitter, QToolButton, QVBoxLayout, QWidget)

from ldmv.core.edits import Editor
from ldmv.core.silence import db_to_display, levels_db, subtract, total_duration
from ldmv.core.timecode import US_PER_SECOND, format_timecode, frame_duration
from ldmv.media import ffmpeg
from ldmv.ui import icons
from ldmv.ui.player import TimelinePlayer
from ldmv.ui.silence_panel import SilencePanel
from ldmv.ui.timeline_widget import TimelineWidget

MEDIA_FILTER = "Médias (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mp3 *.wav *.m4a *.aac *.flac *.ogg);;Tous (*)"


# --------------------------------------------------------------------------
# Tâches en arrière-plan
# --------------------------------------------------------------------------

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float)


class Task(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.fn(*self.args))
        except Exception as e:  # remonté à l'interface
            self.signals.failed.emit(str(e))


def analyse_media(path: str):
    """Import : sonde le fichier, décode l'audio, calcule forme d'onde et niveaux."""
    source = ffmpeg.probe(path)
    analysis = None
    if source.has_audio:
        samples = ffmpeg.load_audio(path)
        db, hop = levels_db(samples, ffmpeg.ANALYSIS_RATE, 20)
        source.peaks = db_to_display(db)
        source.peaks_rate = ffmpeg.ANALYSIS_RATE / hop
        analysis = (db, hop, ffmpeg.ANALYSIS_RATE, len(samples))
    return source, analysis


def grab_frame(path: str, t_us: int, request_id: int):
    out = subprocess.run(
        [ffmpeg.ffmpeg_exe(), "-v", "error", "-ss", f"{t_us / US_PER_SECOND:.3f}", "-i", path,
         "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    return request_id, out.stdout


# --------------------------------------------------------------------------
# Fenêtre
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LDMV — Montage")
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self.editor = Editor()
        self.analyses: dict[str, tuple] = {}
        self.pool = QThreadPool.globalInstance()
        self._tasks: set[Task] = set()
        self._frame_request = 0

        self.timeline_view = TimelineWidget(self.editor)
        self.player = TimelinePlayer(self.editor, self)
        self.player.positionChanged.connect(self._on_play_position)
        self.player.playingChanged.connect(self._on_playing)
        self.player.frameReady.connect(self._show_image)
        self._build_actions()
        self._build_ui()
        self._build_menus()

        tv = self.timeline_view
        tv.changed.connect(self._on_changed)
        tv.playheadChanged.connect(self._on_user_seek)
        tv.selectionChanged.connect(self._refresh_actions)
        tv.toolChanged.connect(self._on_tool)
        tv.thresholdDragged.connect(self.silence_panel.set_threshold)

        sp = self.silence_panel
        sp.thresholdChanged.connect(tv.set_threshold_line)
        sp.previewChanged.connect(self._set_silence_preview)
        sp.applyRequested.connect(self._apply_silences)
        sp.closeRequested.connect(lambda: self.toggle_silence_mode(False))

        self._frame_timer = QTimer(self, singleShot=True, interval=80)
        self._frame_timer.timeout.connect(self._request_frame)
        self._refresh_actions()

    # -- actions et raccourcis -----------------------------------------------
    def _action(self, text, slot, shortcut=None, icon=None, checkable=False, tip=None):
        a = QAction(text, self)
        if icon:
            a.setIcon(icon)
        if shortcut:
            a.setShortcuts([QKeySequence(s) for s in (shortcut if isinstance(shortcut, list) else [shortcut])])
        a.setCheckable(checkable)
        label = tip or text
        if shortcut:
            first = shortcut[0] if isinstance(shortcut, list) else shortcut
            label += f" ({QKeySequence(first).toString(QKeySequence.NativeText)})"
        a.setToolTip(label)
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        tv = self.timeline_view
        self.act_import = self._action("Importer…", self.import_dialog, "Ctrl+I")
        self.act_export = self._action("Exporter…", self.export_dialog, "Ctrl+E")
        self.act_select = self._action("Sélection", lambda: tv.set_tool("select"), ["A", "V"],
                                       icons.cursor_icon(), True, "Outil Sélection")
        self.act_scissors = self._action("Ciseau", lambda: tv.set_tool("split"), "B",
                                         icons.scissors_icon(), True, "Outil Ciseau")
        group = QActionGroup(self)
        group.addAction(self.act_select)
        group.addAction(self.act_scissors)
        self.act_select.setChecked(True)

        self.act_undo = self._action("Annuler", self.undo, "Ctrl+Z", icons.undo_icon())
        self.act_redo = self._action("Rétablir", self.redo, ["Ctrl+Shift+Z", "Ctrl+Y"], icons.redo_icon())
        self.act_split = self._action("Diviser à la tête de lecture", self.split_at_playhead, "Ctrl+B",
                                      icons.split_icon())
        self.act_del_left = self._action("Supprimer à gauche", lambda: self._delete_side("left"), "Q")
        self.act_del_right = self._action("Supprimer à droite", lambda: self._delete_side("right"), "W")
        self.act_delete = self._action("Supprimer", self.delete_selection, ["Delete", "Backspace"],
                                       icons.trash_icon())
        self.act_select_all = self._action("Tout sélectionner", self.select_all, "Ctrl+A")

        self.act_magnet = self._action("Aimant de piste principale", self.toggle_magnet, "N",
                                       icons.magnet_icon(), True)
        self.act_magnet.setChecked(self.editor.timeline.magnet)
        self.act_align = self._action("Alignement automatique", self.toggle_align, "S",
                                      icons.align_icon(), True)
        self.act_align.setChecked(self.editor.timeline.auto_align)
        self.act_silence = self._action("Supprimer les silences", self.toggle_silence_mode, "Ctrl+Shift+S",
                                        icons.silence_icon(), True,
                                        "Supprimer les silences : glissez la barre orange sur la piste")

        self.act_zoom_in = self._action("Zoom avant", lambda: self._zoom(1.4), ["Ctrl+=", "Ctrl++"],
                                        icons.zoom_in_icon())
        self.act_zoom_out = self._action("Zoom arrière", lambda: self._zoom(1 / 1.4), "Ctrl+-",
                                         icons.zoom_out_icon())
        self.act_zoom_fit = self._action("Ajuster à la fenêtre", self.zoom_fit, "Shift+Z")

        frame = lambda: frame_duration(self.editor.timeline.fps)
        self._action("Image précédente", lambda: self._step(-frame()), "Left")
        self._action("Image suivante", lambda: self._step(frame()), "Right")
        self._action("Reculer 1 s", lambda: self._step(-US_PER_SECOND), "Shift+Left")
        self._action("Avancer 1 s", lambda: self._step(US_PER_SECOND), "Shift+Right")
        self.act_play = self._action("Lecture", self.player.toggle, "Space", icons.play_icon(),
                                     tip="Lecture / Pause")
        self.act_to_start = self._action("Début", lambda: self._seek(0), "Home", icons.to_start_icon(),
                                         tip="Revenir au début")
        self._action("Fin", lambda: self._seek(self.editor.timeline.duration), "End")

    def _tool_button(self, action) -> QToolButton:
        b = QToolButton()
        b.setDefaultAction(action)
        b.setAutoRaise(True)
        b.setIconSize(QSize(20, 20))
        return b

    def _separator(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.VLine)
        f.setStyleSheet("color: #3a3a3a;")
        return f

    def _build_ui(self):
        # Médiathèque
        self.media_list = QListWidget()
        self.media_list.itemDoubleClicked.connect(self._append_from_bin)
        import_btn = QPushButton("+  Importer")
        import_btn.clicked.connect(self.import_dialog)
        bin_panel = QWidget()
        bl = QVBoxLayout(bin_panel)
        bl.setContentsMargins(8, 8, 8, 8)
        bl.addWidget(import_btn)
        bl.addWidget(self.media_list, 1)
        bl.addWidget(QLabel("Double-clic : ajouter à la timeline"))

        # Aperçu
        self.preview = QLabel("Importez une vidéo (Ctrl+I ou glisser-déposer)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self.preview.setStyleSheet("background: #000; color: #777;")
        self.timecode = QLabel("00:00:00:00")
        self.timecode.setStyleSheet("font-family: monospace; color: #22d3ee;")
        self.play_btn = self._tool_button(self.act_play)
        self.play_btn.setIconSize(QSize(28, 28))
        controls = QHBoxLayout()
        controls.addWidget(self.timecode)
        controls.addStretch(1)
        controls.addWidget(self._tool_button(self.act_to_start))
        controls.addWidget(self.play_btn)
        controls.addStretch(1)
        controls.addSpacing(self.timecode.sizeHint().width() + 120)
        preview_panel = QWidget()
        pl = QVBoxLayout(preview_panel)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.addWidget(self.preview, 1)
        pl.addLayout(controls)

        top = QSplitter(Qt.Horizontal)
        top.addWidget(bin_panel)
        top.addWidget(preview_panel)
        top.setSizes([350, 1150])

        # Barre d'outils de la timeline (inspirée de CapCut)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 2, 8, 2)
        for item in (self.act_select, self.act_scissors, None, self.act_undo, self.act_redo, None,
                     self.act_split, self.act_delete):
            bar.addWidget(self._separator() if item is None else self._tool_button(item))
        bar.addStretch(1)
        bar.addWidget(self._tool_button(self.act_silence))
        bar.addWidget(self._separator())
        self.magnet_btn = self._tool_button(self.act_magnet)
        self.align_btn = self._tool_button(self.act_align)
        bar.addWidget(self.magnet_btn)
        bar.addWidget(self.align_btn)
        bar.addWidget(self._separator())
        bar.addWidget(self._tool_button(self.act_zoom_out))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, 1000)
        self.zoom_slider.setFixedWidth(140)
        self.zoom_slider.valueChanged.connect(self._zoom_from_slider)
        bar.addWidget(self.zoom_slider)
        bar.addWidget(self._tool_button(self.act_zoom_in))
        bar_widget = QWidget()
        bar_widget.setLayout(bar)
        bar_widget.setStyleSheet("background: #1e1e1e;")

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.timeline_view)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        timeline_panel = QWidget()
        tl = QVBoxLayout(timeline_panel)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        tl.addWidget(bar_widget)
        self.silence_panel = SilencePanel()
        self.silence_panel.hide()
        tl.addWidget(self.silence_panel)
        tl.addWidget(self.scroll, 1)

        main = QSplitter(Qt.Vertical)
        main.addWidget(top)
        main.addWidget(timeline_panel)
        main.setSizes([480, 420])
        self.setCentralWidget(main)
        self._sync_zoom_slider()

    def _build_menus(self):
        m = self.menuBar()
        f = m.addMenu("&Fichier")
        f.addActions([self.act_import, self.act_export])
        f.addSeparator()
        f.addAction(self._action("Quitter", self.close, "Ctrl+Q"))
        e = m.addMenu("&Édition")
        e.addActions([self.act_undo, self.act_redo])
        e.addSeparator()
        e.addActions([self.act_split, self.act_del_left, self.act_del_right, self.act_delete, self.act_select_all])
        t = m.addMenu("&Outils")
        t.addActions([self.act_select, self.act_scissors])
        t.addSeparator()
        t.addActions([self.act_magnet, self.act_align])
        t.addSeparator()
        t.addAction(self.act_silence)
        v = m.addMenu("&Affichage")
        v.addActions([self.act_zoom_in, self.act_zoom_out, self.act_zoom_fit])

    # -- import ----------------------------------------------------------------
    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Importer des médias", "", MEDIA_FILTER)
        for path in paths:
            self.import_media(path)

    def import_media(self, path: str):
        self.statusBar().showMessage(f"Analyse de {path}…")
        task = Task(analyse_media, path)
        task.signals.done.connect(lambda r, t=task: self._on_imported(r, t))
        task.signals.failed.connect(lambda msg, t=task: self._on_task_failed(msg, t))
        self._tasks.add(task)
        self.pool.start(task)

    def _on_imported(self, result, task):
        self._tasks.discard(task)
        source, analysis = result
        if analysis is not None:
            self.analyses[source.id] = analysis
        if source.has_video and not self.editor.timeline.sources:
            self.editor.timeline.fps = source.fps
        item = QListWidgetItem(f"{source.name}\n{format_timecode(source.duration, source.fps)}")
        item.setData(Qt.UserRole, source)
        self.media_list.addItem(item)
        self.editor.append_source(source)
        self._on_changed()
        if len(self.editor.timeline.main_track.clips) == 1:
            self.zoom_fit()
        self.statusBar().showMessage(f"{source.name} importé", 4000)

    def _append_from_bin(self, item: QListWidgetItem):
        self.editor.append_source(item.data(Qt.UserRole))
        self._on_changed()

    def _on_task_failed(self, msg: str, task):
        self._tasks.discard(task)
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Erreur", msg)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.import_media(url.toLocalFile())

    # -- export -------------------------------------------------------------------
    def export_dialog(self):
        if not self.editor.timeline.main_track.clips:
            QMessageBox.information(self, "Export", "La piste principale est vide.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter", "export.mp4", "MP4 (*.mp4)")
        if not path:
            return
        total = sum(c.duration for c in self.editor.timeline.main_track.clips) / US_PER_SECOND

        def on_line(line: str):
            if "time=" in line:
                try:
                    hh, mm, ss = line.split("time=")[1].split()[0].split(":")
                    task.signals.progress.emit((int(hh) * 3600 + int(mm) * 60 + float(ss)) / max(total, 1e-6))
                except ValueError:
                    pass

        task = Task(ffmpeg.export, self.editor.timeline, path, None, on_line)
        task.signals.progress.connect(
            lambda f: self.statusBar().showMessage(f"Export… {min(100, f * 100):.0f} %"))
        task.signals.done.connect(lambda _: (self._tasks.discard(task),
                                             self.statusBar().showMessage(f"Exporté : {path}", 8000)))
        task.signals.failed.connect(lambda msg: self._on_task_failed(msg, task))
        self._tasks.add(task)
        self.pool.start(task)

    # -- édition -------------------------------------------------------------------
    def undo(self):
        self.editor.undo()
        self._on_changed()

    def redo(self):
        self.editor.redo()
        self._on_changed()

    def split_at_playhead(self):
        self.editor.split_at_playhead()
        self._on_changed()

    def _delete_side(self, side):
        self.editor.delete_side_of_playhead(side)
        self._on_changed()
        self._on_playhead(self.editor.timeline.playhead)

    def delete_selection(self):
        self.editor.delete()
        self._on_changed()

    def select_all(self):
        self.editor.selection = {c.id for _, c in self.editor.timeline.all_clips()}
        self._on_changed()

    def toggle_magnet(self, checked: bool):
        tl = self.editor.timeline
        tl.magnet = checked
        if checked:  # activer l'aimant recolle immédiatement la piste principale
            before = self.editor.begin()
            tl.main_track.compact()
            self.editor.commit(before)
        self._on_changed()

    def toggle_align(self, checked: bool):
        self.editor.timeline.auto_align = checked

    def toggle_silence_mode(self, checked: bool):
        """Affiche / masque la barre de seuil et le panneau des silences."""
        if not checked:
            self.silence_panel.hide()
            self.act_silence.setChecked(False)
            self.timeline_view.set_threshold_line(None)
            self._set_silence_preview({})
            return
        tl = self.editor.timeline
        ids = {c.source_id for c in tl.main_track.clips}
        analyses = {sid: a for sid, a in self.analyses.items() if sid in ids}
        if not analyses:
            self.act_silence.setChecked(False)
            QMessageBox.information(self, "Silences", "Aucun clip avec de l'audio sur la piste principale.")
            return
        self.act_silence.setChecked(True)
        self.silence_panel.open(analyses, tl.magnet, self._measure_silences)

    def _measure_silences(self, silences: dict) -> str:
        """Résumé de ce qui sera retiré, limité aux parties présentes sur la timeline."""
        tl = self.editor.timeline
        count = removed = 0
        for clip in tl.main_track.clips:
            holes = silences.get(clip.source_id, [])
            kept = subtract((clip.source_in, clip.source_out), holes)
            removed += clip.duration - total_duration(kept)
            count += sum(1 for a, b in holes if a < clip.source_out and b > clip.source_in)
        total = sum(c.duration for c in tl.main_track.clips)
        pct = 100 * removed / total if total else 0
        plural = "s" if count > 1 else ""
        return f"{count} silence{plural} — {format_timecode(removed, tl.fps)} à retirer ({pct:.0f} %)"

    def _apply_silences(self):
        tl = self.editor.timeline
        panel = self.silence_panel
        report = self.editor.remove_silences(panel.result_silences, ripple=panel.ripple.isChecked())
        self.toggle_silence_mode(False)
        self._on_changed()
        self.statusBar().showMessage(
            f"{report.cuts} silence(s) supprimé(s) ({format_timecode(report.removed_duration, tl.fps)}) — "
            f"{report.clips_before} → {report.clips_after} clips. Ctrl+Z pour annuler.", 10000)

    def _set_silence_preview(self, silences: dict):
        self.timeline_view.preview = silences
        self.timeline_view.update()

    # -- navigation -------------------------------------------------------------------
    def _step(self, delta: int):
        self._seek(self.editor.timeline.playhead + delta)

    def _seek(self, t: int):
        self.player.pause()
        self.editor.timeline.playhead = max(0, t)
        self._on_playhead(self.editor.timeline.playhead)
        self.timeline_view.update()

    def _zoom(self, factor: float):
        self.timeline_view.set_zoom(self.timeline_view.pps * factor)
        self._sync_zoom_slider()

    def zoom_fit(self):
        duration = self.editor.timeline.duration / US_PER_SECOND
        if duration > 0:
            self.timeline_view.set_zoom((self.scroll.viewport().width() - 60) / duration)
            self._sync_zoom_slider()

    # Curseur logarithmique : 0.5 à 2000 px/s
    def _zoom_from_slider(self, value: int):
        self.timeline_view.set_zoom(0.5 * (4000 ** (value / 1000)))

    def _sync_zoom_slider(self):
        import math
        v = round(1000 * math.log(self.timeline_view.pps / 0.5) / math.log(4000))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(v)
        self.zoom_slider.blockSignals(False)

    # -- rafraîchissement ---------------------------------------------------------------
    def _on_changed(self):
        self.player.pause()  # toute modification de la timeline arrête la lecture
        if self.silence_panel.isVisible():
            self.silence_panel.recompute()
        self.timeline_view.update_size()
        self._refresh_actions()
        self._on_playhead(self.editor.timeline.playhead)

    def _refresh_actions(self):
        self.act_undo.setEnabled(bool(self.editor.history.undo_stack))
        self.act_redo.setEnabled(bool(self.editor.history.redo_stack))
        self.act_delete.setEnabled(bool(self.editor.selection))
        self.timeline_view.update()

    def _on_tool(self, tool: str):
        (self.act_scissors if tool == "split" else self.act_select).setChecked(True)

    def _on_playhead(self, t: int):
        self.timecode.setText(f"{format_timecode(t, self.editor.timeline.fps)} / "
                              f"{format_timecode(self.editor.timeline.duration, self.editor.timeline.fps)}")
        if not self.player.playing:
            self._frame_timer.start()

    # -- lecture -----------------------------------------------------------------------
    def _on_user_seek(self, t: int):
        self.player.pause()
        self._on_playhead(t)

    def _on_play_position(self, t: int):
        self._on_playhead(t)
        self.timeline_view.update()
        # La timeline défile pour garder la tête de lecture visible.
        bar = self.scroll.horizontalScrollBar()
        x = int(self.timeline_view.x_of(t))
        width = self.scroll.viewport().width()
        if x < bar.value() or x > bar.value() + width - 40:
            bar.setValue(x - 40)

    def _on_playing(self, playing: bool):
        self.act_play.setIcon(icons.pause_icon() if playing else icons.play_icon())
        self.act_play.setText("Pause" if playing else "Lecture")
        if playing:
            self._frame_timer.stop()

    def _show_image(self, image):
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # -- aperçu image ------------------------------------------------------------------
    def _request_frame(self):
        tl = self.editor.timeline
        for track in tl.tracks:  # piste du haut prioritaire
            clip = track.clip_at(tl.playhead)
            if clip and tl.sources[clip.source_id].has_video:
                self._frame_request += 1
                task = Task(grab_frame, tl.sources[clip.source_id].path,
                            clip.source_time(tl.playhead), self._frame_request)
                task.signals.done.connect(lambda r, t=task: self._show_frame(r, t))
                task.signals.failed.connect(lambda _m, t=task: self._tasks.discard(t))
                self._tasks.add(task)
                self.pool.start(task)
                return
        self.preview.setPixmap(QPixmap())
        self.preview.setText("")

    def _show_frame(self, result, task):
        self._tasks.discard(task)
        request_id, data = result
        if request_id != self._frame_request or not data:
            return  # réponse périmée
        if self.player.playing:
            return
        pm = QPixmap()
        pm.loadFromData(data, "PNG")
        self.preview.setPixmap(pm.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in [
        (QPalette.Window, "#1e1e1e"), (QPalette.WindowText, "#dcdcdc"), (QPalette.Base, "#181818"),
        (QPalette.AlternateBase, "#232323"), (QPalette.Text, "#dcdcdc"), (QPalette.Button, "#2a2a2a"),
        (QPalette.ButtonText, "#dcdcdc"), (QPalette.Highlight, "#22d3ee"), (QPalette.HighlightedText, "#000000"),
        (QPalette.ToolTipBase, "#2a2a2a"), (QPalette.ToolTipText, "#dcdcdc"),
    ]:
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet("""
        QToolButton { padding: 4px; border-radius: 4px; }
        QToolButton:hover { background: #333; }
        QToolButton:checked { background: #10343a; }
        QSplitter::handle { background: #111; }
    """)


def run(paths: list[str]) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_dark_theme(app)
    win = MainWindow()
    win.show()
    for path in paths:
        win.import_media(path)
    return app.exec()
