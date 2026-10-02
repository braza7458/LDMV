"""Opérations d'édition sur la timeline (Parties 1, 2 et 3).

Toutes les fonctions sont pures vis-à-vis de l'interface : elles modifient
un `Timeline` et peuvent être testées sans fenêtre. La classe `Editor`
les enveloppe avec l'historique (annuler / rétablir) : chaque appel public
= une seule entrée d'historique, même s'il touche des centaines de clips.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ldmv.core.model import MIN_CLIP_DURATION, Clip, Timeline, Track, new_id
from ldmv.core.silence import Interval, subtract, total_duration


# --------------------------------------------------------------------------
# Partie 3 — Roll edit (édition de frontière)
# --------------------------------------------------------------------------
#
# Deux clips adjacents G (gauche) et D (droite), G.end == D.start.
# Faire glisser la jonction de Δ (µs, positif = vers la droite) :
#
#     G.source_out' = G.source_out + Δ     (G gagne/perd des images à la fin)
#     D.source_in'  = D.source_in  + Δ     (D perd/gagne des images au début)
#     D.start'      = D.start      + Δ     (la jonction se déplace)
#     G.start et D.end ne bougent pas  =>  durée totale G+D constante :
#
#     (G.duration + Δ) + (D.duration - Δ) = G.duration + D.duration
#
# Contraintes (on borne Δ au lieu de refuser le geste) :
#     G.source_out + Δ <= G.source_duration    (pas au-delà de la fin du fichier de G)
#     D.source_in  + Δ >= 0                    (pas avant le début du fichier de D)
#     G.duration   + Δ >= MIN                  (G garde au moins une image)
#     D.duration   - Δ >= MIN                  (D garde au moins une image)
#
#  =>  Δ_min = max(-(G.duration - MIN), -D.source_in)
#      Δ_max = min(G.source_duration - G.source_out, D.duration - MIN)


def roll_limits(left: Clip, right: Clip, min_duration: int = MIN_CLIP_DURATION) -> tuple[int, int]:
    lo = max(-(left.duration - min_duration), -right.source_in)
    hi = min(left.source_duration - left.source_out, right.duration - min_duration)
    return lo, hi


def roll_edit(left: Clip, right: Clip, delta: int, min_duration: int = MIN_CLIP_DURATION) -> int:
    """Déplace la jonction entre `left` et `right`. Renvoie le Δ réellement appliqué."""
    if left.end != right.start:
        raise ValueError("Le roll edit exige deux clips adjacents")
    lo, hi = roll_limits(left, right, min_duration)
    delta = max(lo, min(hi, delta))
    left.source_out += delta
    right.source_in += delta
    right.start += delta
    return delta


# --------------------------------------------------------------------------
# Partie 2 — Outils de base : découper, supprimer, déplacer, rogner
# --------------------------------------------------------------------------

def split_clip(track: Track, clip: Clip, t: int) -> tuple[Clip, Clip] | None:
    """Outil ciseau : coupe `clip` au temps timeline `t`."""
    if not (clip.start + MIN_CLIP_DURATION <= t <= clip.end - MIN_CLIP_DURATION):
        return None
    cut = clip.source_time(t)
    right = Clip(
        source_id=clip.source_id,
        start=t,
        source_in=cut,
        source_out=clip.source_out,
        source_duration=clip.source_duration,
    )
    clip.source_out = cut
    track.clips.insert(track.index_of(clip.id) + 1, right)
    return clip, right


def apply_magnet(timeline: Timeline, track: Track) -> None:
    """L'aimant agit uniquement sur la piste principale, comme dans CapCut."""
    if timeline.magnet and track.is_main:
        track.compact()


# --------------------------------------------------------------------------
# Partie 1 — Suppression groupée des silences
# --------------------------------------------------------------------------

@dataclass
class SilenceRemovalReport:
    clips_before: int = 0
    clips_after: int = 0
    removed_duration: int = 0  # µs retirés de la piste
    cuts: int = 0               # nombre de segments silencieux supprimés


def remove_silences(track: Track, silences_by_source: dict[str, list[Interval]],
                    ripple: bool) -> SilenceRemovalReport:
    """Coupe au début et à la fin de chaque silence puis supprime tous les
    segments silencieux, en une seule passe sur la piste.

    `silences_by_source` : intervalles en temps *source* (sortie de
    `detect_silences`), ce qui marche quel que soit le découpage existant
    des clips sur la timeline.

    `ripple` : recolle les clips restants (magnétisme). Sinon, les clips
    gardent leur position et des vides apparaissent.
    """
    report = SilenceRemovalReport(clips_before=len(track.clips))
    track.sort()
    new_clips: list[Clip] = []
    for clip in track.clips:
        holes = silences_by_source.get(clip.source_id, [])
        keeps = subtract((clip.source_in, clip.source_out), holes)
        keeps = [(a, b) for a, b in keeps if b - a >= MIN_CLIP_DURATION]
        removed = clip.duration - total_duration(keeps)
        if removed == 0:
            new_clips.append(clip)
            continue
        report.removed_duration += removed
        report.cuts += sum(1 for h0, h1 in holes if h0 < clip.source_out and h1 > clip.source_in)
        for i, (a, b) in enumerate(keeps):
            new_clips.append(Clip(
                source_id=clip.source_id,
                start=clip.timeline_time(a),
                source_in=a,
                source_out=b,
                source_duration=clip.source_duration,
                id=clip.id if i == 0 else new_id(),
            ))
    track.clips = new_clips
    if ripple:
        track.compact()
    report.clips_after = len(track.clips)
    return report


# --------------------------------------------------------------------------
# Historique et façade utilisée par l'interface
# --------------------------------------------------------------------------

@dataclass
class _Snapshot:
    tracks: list[Track]
    playhead: int


class History:
    """Annuler / rétablir par instantanés des pistes (les sources, lourdes et
    immuables, ne sont pas copiées)."""

    def __init__(self, limit: int = 200):
        self.undo_stack: list[_Snapshot] = []
        self.redo_stack: list[_Snapshot] = []
        self.limit = limit

    @staticmethod
    def capture(timeline: Timeline) -> _Snapshot:
        return _Snapshot(copy.deepcopy(timeline.tracks), timeline.playhead)

    @staticmethod
    def restore(timeline: Timeline, snap: _Snapshot) -> None:
        timeline.tracks = copy.deepcopy(snap.tracks)
        timeline.playhead = snap.playhead

    def push(self, snap: _Snapshot) -> None:
        self.undo_stack.append(snap)
        del self.undo_stack[: -self.limit]
        self.redo_stack.clear()

    def undo(self, timeline: Timeline) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.capture(timeline))
        self.restore(timeline, self.undo_stack.pop())
        return True

    def redo(self, timeline: Timeline) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.capture(timeline))
        self.restore(timeline, self.redo_stack.pop())
        return True


@dataclass
class Editor:
    timeline: Timeline = field(default_factory=Timeline)
    history: History = field(default_factory=History)
    selection: set[str] = field(default_factory=set)
    _gesture: _Snapshot | None = field(default=None, repr=False)

    # -- transactions ------------------------------------------------------
    def begin(self) -> _Snapshot:
        return History.capture(self.timeline)

    def commit(self, before: _Snapshot) -> None:
        if History.capture(self.timeline).tracks != before.tracks:
            self.history.push(before)

    # Un geste souris (glisser un clip, une jonction…) appelle l'opération à
    # chaque mouvement en repartant de l'état initial, puis ne crée qu'une
    # seule entrée d'historique au relâchement.
    def begin_gesture(self) -> None:
        self._gesture = self.begin()

    def reset_gesture(self) -> None:
        if self._gesture is not None:
            History.restore(self.timeline, self._gesture)

    def end_gesture(self) -> None:
        before, self._gesture = self._gesture, None
        if before is not None:
            self.commit(before)

    @property
    def in_gesture(self) -> bool:
        return self._gesture is not None

    def _run(self, fn, *args, **kwargs):
        if self.in_gesture:
            return fn(*args, **kwargs)
        before = self.begin()
        result = fn(*args, **kwargs)
        self.commit(before)
        return result

    def undo(self) -> bool:
        ok = self.history.undo(self.timeline)
        self._prune_selection()
        return ok

    def redo(self) -> bool:
        ok = self.history.redo(self.timeline)
        self._prune_selection()
        return ok

    def _prune_selection(self) -> None:
        ids = {c.id for _, c in self.timeline.all_clips()}
        self.selection &= ids

    # -- médias ------------------------------------------------------------
    def append_source(self, source, track: Track | None = None) -> Clip:
        def do():
            self.timeline.add_source(source)
            target = track or self.timeline.main_track
            clip = Clip(source.id, target.end, 0, source.duration, source.duration)
            target.clips.append(clip)
            apply_magnet(self.timeline, target)
            return clip
        return self._run(do)

    # -- ciseau --------------------------------------------------------------
    def split(self, clip_id: str, t: int) -> bool:
        def do():
            track, clip = self.timeline.find(clip_id)
            return split_clip(track, clip, t) is not None
        return self._run(do)

    def split_at_playhead(self) -> int:
        """Ctrl+B : coupe à la tête de lecture les clips sélectionnés,
        ou tous les clips sous la tête de lecture si rien n'est sélectionné."""
        def do():
            t = self.timeline.playhead
            count = 0
            for track in self.timeline.tracks:
                clip = track.clip_at(t)
                if clip and (not self.selection or clip.id in self.selection):
                    count += split_clip(track, clip, t) is not None
            return count
        return self._run(do)

    # -- suppression ---------------------------------------------------------
    def delete(self, clip_ids: set[str] | None = None) -> int:
        ids = set(clip_ids if clip_ids is not None else self.selection)

        def do():
            removed = 0
            for track in self.timeline.tracks:
                before = len(track.clips)
                track.clips = [c for c in track.clips if c.id not in ids]
                if len(track.clips) != before:
                    removed += before - len(track.clips)
                    apply_magnet(self.timeline, track)
            return removed
        removed = self._run(do)
        self.selection -= ids
        return removed

    def delete_side_of_playhead(self, side: str) -> int:
        """Q / W dans CapCut : supprime la partie gauche ou droite des clips
        sous la tête de lecture (sélectionnés, ou tous si aucune sélection)."""
        def do():
            t = self.timeline.playhead
            count = 0
            for track in self.timeline.tracks:
                clip = track.clip_at(t)
                if not clip or (self.selection and clip.id not in self.selection):
                    continue
                parts = split_clip(track, clip, t)
                if not parts:
                    continue
                left, right = parts
                track.clips.remove(left if side == "left" else right)
                apply_magnet(self.timeline, track)
                if side == "left" and self.timeline.magnet and track.is_main:
                    self.timeline.playhead = right.start
                count += 1
            return count
        return self._run(do)

    # -- déplacement ---------------------------------------------------------
    def move(self, clip_id: str, new_start: int) -> bool:
        """Déplace un clip. Sur la piste principale aimantée, le clip est
        réinséré à l'endroit du dépôt et la piste est recollée. Sinon, le
        déplacement est refusé s'il chevauche un autre clip."""
        return self._run(self._move, clip_id, new_start)

    def _move(self, clip_id: str, new_start: int) -> bool:
        track, clip = self.timeline.find(clip_id)
        new_start = max(0, new_start)
        if self.timeline.magnet and track.is_main:
            others = [c for c in track.clips if c.id != clip_id]
            centre = new_start + clip.duration // 2
            index = sum(1 for c in others if c.start + c.duration // 2 < centre)
            others.insert(index, clip)
            t = 0
            for c in others:
                c.start = t
                t = c.end
            track.clips = others
            ok = True
        elif track.overlaps(new_start, new_start + clip.duration, ignore_id=clip_id):
            ok = False
        else:
            clip.start = new_start
            track.sort()
            ok = True
        return ok

    # -- rognage d'un bord ---------------------------------------------------
    def trim(self, clip_id: str, edge: str, t: int) -> None:
        """Rogne le bord 'left' ou 'right' d'un clip jusqu'au temps timeline t."""
        def do():
            track, clip = self.timeline.find(clip_id)
            if edge == "left":
                lo = max(clip.start - clip.source_in, self._free_before(track, clip))
                t2 = max(lo, min(t, clip.end - MIN_CLIP_DURATION))
                clip.source_in += t2 - clip.start
                clip.start = t2
            else:
                hi = min(clip.start + clip.source_duration - clip.source_in, self._free_after(track, clip))
                t2 = min(hi, max(t, clip.start + MIN_CLIP_DURATION))
                clip.source_out = clip.source_in + (t2 - clip.start)
            apply_magnet(self.timeline, track)
        self._run(do)

    def _free_before(self, track: Track, clip: Clip) -> int:
        if self.timeline.magnet and track.is_main:
            return -(10**18)
        return max((c.end for c in track.clips if c.end <= clip.start), default=0)

    def _free_after(self, track: Track, clip: Clip) -> int:
        if self.timeline.magnet and track.is_main:
            return 10**18
        return min((c.start for c in track.clips if c.start >= clip.end), default=10**18)

    # -- roll edit -----------------------------------------------------------
    def roll(self, left_id: str, right_id: str, delta: int) -> int:
        def do():
            _, left = self.timeline.find(left_id)
            _, right = self.timeline.find(right_id)
            return roll_edit(left, right, delta)
        return self._run(do)

    # -- suppression des silences -------------------------------------------
    def remove_silences(self, silences_by_source: dict[str, list[Interval]],
                        track: Track | None = None, ripple: bool | None = None) -> SilenceRemovalReport:
        """Une seule entrée d'historique : Ctrl+Z restaure tout d'un coup."""
        def do():
            target = track or self.timeline.main_track
            rip = (self.timeline.magnet and target.is_main) if ripple is None else ripple
            return remove_silences(target, silences_by_source, rip)
        report = self._run(do)
        self._prune_selection()
        return report
