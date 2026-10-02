"""Partie 1 — Détection des silences.

Algorithme :
  1. Découper le signal en fenêtres courtes (20 ms par défaut).
  2. Calculer le niveau RMS de chaque fenêtre, converti en dBFS :
         dB = 20 · log10(RMS)          (0 dBFS = pleine échelle)
  3. Une fenêtre est « silencieuse » si dB < seuil.
  4. Les petits sursauts de son isolés (clic, souffle) plus courts que
     `min_sound_ms` au milieu d'un silence sont ignorés.
  5. Seuls les silences d'au moins `min_silence_ms` sont retenus.
  6. Une marge (`padding_ms`) est rendue de chaque côté de la parole pour
     ne pas couper les attaques et fins de mots.

Le résultat est une liste d'intervalles (début, fin) en µs dans le temps
du fichier source ; `ldmv.core.edits.remove_silences` les applique ensuite
à la timeline en une seule opération groupée.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ldmv.core.timecode import US_PER_SECOND

Interval = tuple[int, int]


@dataclass
class SilenceParams:
    threshold_db: float = -40.0   # sous ce niveau = silence
    min_silence_ms: int = 500     # durée minimale d'un silence à couper
    padding_ms: int = 100         # marge conservée autour de la parole
    min_sound_ms: int = 60        # sons plus courts ignorés (clics)
    window_ms: int = 20           # résolution de l'analyse


def levels_db(samples: np.ndarray, sample_rate: int, window_ms: int = 20) -> tuple[np.ndarray, int]:
    """Niveau RMS (dBFS) par fenêtre. Renvoie (niveaux, taille de fenêtre en échantillons)."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim > 1:  # multicanal -> mono
        samples = samples.mean(axis=1)
    hop = max(1, int(sample_rate * window_ms / 1000))
    n_frames = -(-len(samples) // hop)  # division arrondie au supérieur
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32), hop
    padded = np.zeros(n_frames * hop, dtype=np.float32)
    padded[: len(samples)] = samples
    frames = padded.reshape(n_frames, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    return (20.0 * np.log10(np.maximum(rms, 1e-10))).astype(np.float32), hop


def _runs(mask: np.ndarray) -> list[tuple[int, int, bool]]:
    """Plages consécutives d'un masque booléen : [(début, fin_exclue, valeur)]."""
    if len(mask) == 0:
        return []
    changes = np.flatnonzero(np.diff(mask.astype(np.int8))) + 1
    bounds = np.concatenate(([0], changes, [len(mask)]))
    return [(int(a), int(b), bool(mask[a])) for a, b in zip(bounds[:-1], bounds[1:])]


def silent_mask(db: np.ndarray, params: SilenceParams) -> np.ndarray:
    mask = db < params.threshold_db
    min_sound = max(1, round(params.min_sound_ms / params.window_ms))
    runs = _runs(mask)
    for i, (a, b, silent) in enumerate(runs):
        bounded = 0 < i < len(runs) - 1
        if not silent and bounded and (b - a) < min_sound:
            mask[a:b] = True
    return mask


def detect_silences(samples: np.ndarray, sample_rate: int, params: SilenceParams | None = None) -> list[Interval]:
    """Intervalles silencieux (µs, temps du fichier source), triés, sans chevauchement."""
    params = params or SilenceParams()
    db, hop = levels_db(samples, sample_rate, params.window_ms)
    return silences_from_levels(db, hop, sample_rate, len(samples), params)


def silences_from_levels(db: np.ndarray, hop: int, sample_rate: int, n_samples: int,
                         params: SilenceParams) -> list[Interval]:
    """Comme `detect_silences`, à partir de niveaux déjà calculés (permet de
    rejouer l'analyse instantanément quand l'utilisateur bouge le seuil)."""
    mask = silent_mask(db.copy(), params)
    min_frames = max(1, int(np.ceil(params.min_silence_ms / params.window_ms - 1e-9)))
    total = round(n_samples * US_PER_SECOND / sample_rate)
    pad = params.padding_ms * 1000

    result: list[Interval] = []
    for a, b, silent in _runs(mask):
        if not silent or (b - a) < min_frames:
            continue
        start = round(a * hop * US_PER_SECOND / sample_rate)
        end = min(total, round(b * hop * US_PER_SECOND / sample_rate))
        # Marge uniquement du côté où il y a de la parole.
        if start > 0:
            start += pad
        if end < total:
            end -= pad
        if end > start:
            result.append((start, end))
    return result


def subtract(keep: Interval, holes: list[Interval]) -> list[Interval]:
    """Retire les intervalles `holes` (triés) de l'intervalle `keep`."""
    a, b = keep
    out: list[Interval] = []
    cursor = a
    for h0, h1 in holes:
        if h1 <= cursor or h0 >= b:
            continue
        if h0 > cursor:
            out.append((cursor, h0))
        cursor = max(cursor, h1)
        if cursor >= b:
            break
    if cursor < b:
        out.append((cursor, b))
    return out


def total_duration(intervals: list[Interval]) -> int:
    return sum(b - a for a, b in intervals)
