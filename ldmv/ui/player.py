"""Lecture de la timeline (bouton Play / barre Espace).

La lecture suit la piste principale clip par clip : à la fin d'un clip, le
lecteur saute au point d'entrée du clip suivant. Après une coupe des
silences, on entend donc directement le résultat du montage, sans exporter.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink

from ldmv.core.edits import Editor
from ldmv.core.model import Clip

TICK_MS = 15
END_MARGIN_US = 15_000      # passe au clip suivant un poil avant la fin
SEEK_TOLERANCE_US = 250_000  # position considérée comme atteinte après un saut


class TimelinePlayer(QObject):
    positionChanged = Signal(int)   # nouvelle position de la tête de lecture (µs)
    playingChanged = Signal(bool)
    frameReady = Signal(QImage)     # image à afficher pendant la lecture

    def __init__(self, editor: Editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.audio = QAudioOutput(self)
        # Les images sont converties en QImage et affichées par la fenêtre :
        # fonctionne aussi sur les machines sans accélération graphique.
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.sink)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self._playing = False
        self._clip_id: str | None = None
        self._path: str | None = None
        self._pending_ms: int | None = None   # saut en attente du chargement du fichier
        self._seek_target: int | None = None  # µs source, tant que le saut n'est pas effectif

    @property
    def playing(self) -> bool:
        return self._playing

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    def play(self) -> None:
        tl = self.editor.timeline
        track = tl.main_track
        track.sort()
        if not track.clips:
            return
        clip = track.clip_at(tl.playhead) or self._next_clip(tl.playhead)
        if clip is None:  # tête de lecture en fin de timeline : on repart du début
            clip = track.clips[0]
            tl.playhead = clip.start
        tl.playhead = max(tl.playhead, clip.start)
        self._playing = True
        self._start_clip(clip, clip.source_time(tl.playhead))
        self.timer.start()
        self.playingChanged.emit(True)

    def pause(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self.timer.stop()
        self.player.pause()
        self._pending_ms = None
        self.playingChanged.emit(False)

    # -- interne -------------------------------------------------------------
    def _next_clip(self, t: int) -> Clip | None:
        return next((c for c in self.editor.timeline.main_track.clips if c.start >= t), None)

    def _current_clip(self) -> Clip | None:
        for c in self.editor.timeline.main_track.clips:
            if c.id == self._clip_id:
                return c
        return None

    def _start_clip(self, clip: Clip, source_us: int) -> None:
        self._clip_id = clip.id
        self._seek_target = source_us
        path = self.editor.timeline.sources[clip.source_id].path
        if path != self._path:
            self._path = path
            self._pending_ms = source_us // 1000
            self.player.setSource(QUrl.fromLocalFile(path))
        else:
            self.player.setPosition(source_us // 1000)
            self.player.play()

    def _on_frame(self, frame: QVideoFrame) -> None:
        if self._playing and self._seek_target is None:
            image = frame.toImage()
            if not image.isNull():
                self.frameReady.emit(image)

    def _on_status(self, status) -> None:
        if self._pending_ms is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.player.setPosition(self._pending_ms)
            self._pending_ms = None
            if self._playing:
                self.player.play()

    def _tick(self) -> None:
        if self._pending_ms is not None:
            return
        clip = self._current_clip()
        if clip is None:
            self.pause()
            return
        pos = self.player.position() * 1000
        if self._seek_target is not None:
            # Juste après un saut, position() renvoie encore l'ancienne valeur.
            if abs(pos - self._seek_target) > SEEK_TOLERANCE_US:
                return
            self._seek_target = None

        ended = self.player.mediaStatus() == QMediaPlayer.EndOfMedia
        if pos >= clip.source_out - END_MARGIN_US or ended:
            nxt = self._next_clip(clip.end)
            if nxt is None:
                self.editor.timeline.playhead = clip.end
                self.positionChanged.emit(clip.end)
                self.pause()
                return
            self.editor.timeline.playhead = nxt.start
            self._start_clip(nxt, nxt.source_in)
        else:
            self.editor.timeline.playhead = clip.timeline_time(max(pos, clip.source_in))
        self.positionChanged.emit(self.editor.timeline.playhead)
