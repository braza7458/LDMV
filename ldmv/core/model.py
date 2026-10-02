"""Modèle de données de la timeline.

Vocabulaire (identique pour tous les modules) :

    source_in / source_out : points d'entrée / sortie dans le fichier source
                             (le `trim_in` / `trim_out` du clip), en µs.
    start                  : position du clip sur la timeline, en µs.
    duration               : source_out - source_in.
    end                    : start + duration.

    Fichier source  |-------[source_in ===== source_out]--------|
    Timeline                 [start ======== end]
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

_ids = itertools.count(1)


def new_id(prefix: str = "c") -> str:
    return f"{prefix}{next(_ids)}"


# Durée minimale d'un clip : une image à 60 i/s. Évite les clips de 0 µs.
MIN_CLIP_DURATION = 16_667


@dataclass
class MediaSource:
    """Un fichier importé. Les données lourdes (forme d'onde) ne sont pas
    copiées dans l'historique d'annulation : seuls les clips le sont."""

    path: str
    duration: int
    has_video: bool = True
    has_audio: bool = True
    width: int = 0
    height: int = 0
    fps: float = 30.0
    id: str = field(default_factory=lambda: new_id("s"))
    # Forme d'onde : niveaux 0..1 (voir silence.db_to_display), `peaks_rate` par seconde.
    peaks: object = field(default=None, repr=False, compare=False)
    peaks_rate: float = 50.0

    @property
    def name(self) -> str:
        return self.path.replace("\\", "/").rsplit("/", 1)[-1]


@dataclass
class Clip:
    source_id: str
    start: int
    source_in: int
    source_out: int
    source_duration: int
    id: str = field(default_factory=new_id)

    @property
    def duration(self) -> int:
        return self.source_out - self.source_in

    @property
    def end(self) -> int:
        return self.start + self.duration

    def source_time(self, timeline_time: int) -> int:
        """Convertit un temps de la timeline en temps dans le fichier source."""
        return self.source_in + (timeline_time - self.start)

    def timeline_time(self, source_time: int) -> int:
        return self.start + (source_time - self.source_in)

    def contains(self, t: int) -> bool:
        return self.start <= t < self.end


@dataclass
class Track:
    clips: list[Clip] = field(default_factory=list)
    is_main: bool = False
    name: str = ""
    id: str = field(default_factory=lambda: new_id("t"))

    def sort(self) -> None:
        self.clips.sort(key=lambda c: c.start)

    def clip_at(self, t: int) -> Clip | None:
        for clip in self.clips:
            if clip.contains(t):
                return clip
        return None

    def index_of(self, clip_id: str) -> int:
        for i, clip in enumerate(self.clips):
            if clip.id == clip_id:
                return i
        raise KeyError(clip_id)

    def compact(self) -> None:
        """Magnétisme : recolle les clips bout à bout à partir de 0,
        dans l'ordre actuel, en supprimant tous les vides."""
        self.sort()
        t = 0
        for clip in self.clips:
            clip.start = t
            t = clip.end

    def overlaps(self, start: int, end: int, ignore_id: str | None = None) -> bool:
        return any(
            c.id != ignore_id and c.start < end and start < c.end for c in self.clips
        )

    @property
    def end(self) -> int:
        return max((c.end for c in self.clips), default=0)


@dataclass
class Timeline:
    tracks: list[Track] = field(default_factory=lambda: [Track(is_main=True, name="Principale")])
    sources: dict[str, MediaSource] = field(default_factory=dict)
    magnet: bool = True       # Aimant de piste principale
    auto_align: bool = True   # Alignement automatique (accrochage)
    playhead: int = 0
    fps: float = 30.0

    @property
    def main_track(self) -> Track:
        for track in self.tracks:
            if track.is_main:
                return track
        return self.tracks[0]

    @property
    def duration(self) -> int:
        return max((t.end for t in self.tracks), default=0)

    def add_source(self, source: MediaSource) -> MediaSource:
        self.sources[source.id] = source
        return source

    def find(self, clip_id: str) -> tuple[Track, Clip]:
        for track in self.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return track, clip
        raise KeyError(clip_id)

    def all_clips(self):
        for track in self.tracks:
            yield from ((track, c) for c in track.clips)

    def track_by_id(self, track_id: str) -> Track:
        for track in self.tracks:
            if track.id == track_id:
                return track
        raise KeyError(track_id)
