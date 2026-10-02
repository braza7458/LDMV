import pytest

from ldmv.core.edits import Editor, roll_edit, roll_limits
from ldmv.core.model import MIN_CLIP_DURATION, Clip, MediaSource

S = 1_000_000


def make_editor(*durations, magnet=True):
    ed = Editor()
    ed.timeline.magnet = magnet
    clips = []
    for d in durations:
        src = MediaSource(path=f"f{d}.mp4", duration=d * S)
        clips.append(ed.append_source(src))
    ed.history.undo_stack.clear()
    return ed, clips


def starts(ed):
    return [(c.start, c.end) for c in ed.timeline.main_track.clips]


# --- Partie 3 : roll edit --------------------------------------------------

def test_roll_right_extends_left_and_shortens_right():
    left = Clip("a", 0, 0, 5 * S, 10 * S)
    right = Clip("b", 5 * S, 2 * S, 8 * S, 10 * S)
    total = left.duration + right.duration
    assert roll_edit(left, right, 1 * S) == 1 * S
    assert (left.source_in, left.source_out) == (0, 6 * S)
    assert (right.start, right.source_in, right.source_out) == (6 * S, 3 * S, 8 * S)
    assert left.duration + right.duration == total
    assert left.end == right.start


def test_roll_left_extends_right():
    left = Clip("a", 0, 0, 5 * S, 10 * S)
    right = Clip("b", 5 * S, 2 * S, 8 * S, 10 * S)
    roll_edit(left, right, -1 * S)
    assert left.source_out == 4 * S
    assert (right.start, right.source_in) == (4 * S, 1 * S)


def test_roll_is_clamped_by_available_media():
    # gauche : il ne reste que 1 s dans le fichier ; droite : 2 s avant son début
    left = Clip("a", 0, 0, 9 * S, 10 * S)
    right = Clip("b", 9 * S, 2 * S, 8 * S, 10 * S)
    assert roll_limits(left, right) == (-2 * S, 1 * S)
    assert roll_edit(left, right, 5 * S) == 1 * S
    assert roll_edit(left, right, -50 * S) == -3 * S  # recul jusqu'au début du fichier de droite


def test_roll_keeps_minimum_duration():
    left = Clip("a", 0, 0, 1 * S, 100 * S)
    right = Clip("b", 1 * S, 0, 1 * S, 100 * S)
    roll_edit(left, right, 10 * S)
    assert right.duration == MIN_CLIP_DURATION


def test_roll_requires_adjacent_clips():
    with pytest.raises(ValueError):
        roll_edit(Clip("a", 0, 0, S, 10 * S), Clip("b", 2 * S, 0, S, 10 * S), 1)


# --- Partie 2 : ciseau, suppression, aimant -------------------------------

def test_split_and_undo():
    ed, (clip,) = make_editor(10)
    assert ed.split(clip.id, 4 * S)
    assert starts(ed) == [(0, 4 * S), (4 * S, 10 * S)]
    ed.undo()
    assert starts(ed) == [(0, 10 * S)]
    ed.redo()
    assert len(ed.timeline.main_track.clips) == 2


def test_delete_with_magnet_closes_gap():
    ed, (a, b, c) = make_editor(2, 3, 4)
    ed.delete({b.id})
    assert starts(ed) == [(0, 2 * S), (2 * S, 6 * S)]


def test_delete_without_magnet_leaves_gap():
    ed, (a, b, c) = make_editor(2, 3, 4, magnet=False)
    ed.delete({b.id})
    assert starts(ed) == [(0, 2 * S), (5 * S, 9 * S)]


def test_move_with_magnet_reorders():
    ed, (a, b, c) = make_editor(2, 3, 4)
    ed.move(a.id, 8 * S)  # déposé à la fin
    assert [x.id for x in ed.timeline.main_track.clips] == [b.id, c.id, a.id]
    assert starts(ed) == [(0, 3 * S), (3 * S, 7 * S), (7 * S, 9 * S)]


def test_move_without_magnet_refuses_overlap():
    ed, (a, b) = make_editor(2, 3, magnet=False)
    assert not ed.move(a.id, 3 * S)
    assert ed.move(a.id, 6 * S)
    assert starts(ed) == [(2 * S, 5 * S), (6 * S, 8 * S)]


def test_split_at_playhead():
    ed, _ = make_editor(10)
    ed.timeline.playhead = 3 * S
    assert ed.split_at_playhead() == 1


# --- Partie 1 : coupe groupée des silences ---------------------------------

def test_remove_silences_with_magnet_is_one_undo_step():
    ed, (clip,) = make_editor(10)
    silences = {clip.source_id: [(1 * S, 2 * S), (5 * S, 7 * S)]}
    report = ed.remove_silences(silences)
    assert starts(ed) == [(0, 1 * S), (1 * S, 4 * S), (4 * S, 7 * S)]
    assert [(c.source_in, c.source_out) for c in ed.timeline.main_track.clips] == [
        (0, 1 * S), (2 * S, 5 * S), (7 * S, 10 * S)]
    assert report.removed_duration == 3 * S and report.cuts == 2
    ed.undo()
    assert starts(ed) == [(0, 10 * S)]


def test_remove_silences_without_magnet_keeps_positions():
    ed, (clip,) = make_editor(10, magnet=False)
    ed.remove_silences({clip.source_id: [(1 * S, 2 * S)]})
    assert starts(ed) == [(0, 1 * S), (2 * S, 10 * S)]


def test_remove_silences_respects_existing_trims():
    # clip déjà rogné (source 3..8 s) et placé après un autre clip
    ed, (first, clip) = make_editor(2, 10)
    clip.source_in, clip.source_out = 3 * S, 8 * S
    ed.timeline.main_track.compact()
    ed.remove_silences({clip.source_id: [(0, 4 * S), (6 * S, 7 * S)]})
    assert [(c.source_in, c.source_out) for c in ed.timeline.main_track.clips[1:]] == [
        (4 * S, 6 * S), (7 * S, 8 * S)]
    assert starts(ed)[1:] == [(2 * S, 4 * S), (4 * S, 5 * S)]


def test_delete_left_of_playhead_with_magnet():
    ed, (a, b) = make_editor(4, 6)
    ed.timeline.playhead = 5 * S
    assert ed.delete_side_of_playhead("left") == 1
    assert starts(ed) == [(0, 4 * S), (4 * S, 9 * S)]
    assert ed.timeline.main_track.clips[1].source_in == 1 * S
    assert ed.timeline.playhead == 4 * S


def test_gesture_creates_single_history_entry():
    ed, (a, b) = make_editor(5, 5)
    a.source_out = 3 * S  # 2 s de média disponibles après la fin de `a`
    ed.timeline.main_track.compact()
    ed.begin_gesture()
    for d in (1, 2, 3):
        ed.reset_gesture()
        ed.roll(a.id, b.id, d * S)
    ed.end_gesture()
    assert len(ed.history.undo_stack) == 1
    assert starts(ed) == [(0, 5 * S), (5 * S, 8 * S)]  # Δ borné à +2 s
    ed.undo()
    assert starts(ed) == [(0, 3 * S), (3 * S, 8 * S)]
