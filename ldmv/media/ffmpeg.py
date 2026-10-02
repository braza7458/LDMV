"""Lecture des médias (ffprobe / ffmpeg) et export de la timeline."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import numpy as np

from ldmv.core.model import Clip, MediaSource, Timeline, Track
from ldmv.core.timecode import US_PER_SECOND

ANALYSIS_RATE = 16_000  # Hz : largement suffisant pour détecter la parole
PEAKS_RATE = 100        # valeurs de forme d'onde par seconde


class FFmpegError(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise FFmpegError(f"{tool} introuvable : installez ffmpeg et ajoutez-le au PATH")
    return path


def probe(path: str) -> MediaSource:
    out = subprocess.run(
        [_require("ffprobe"), "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,avg_frame_rate",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise FFmpegError(out.stderr.strip() or f"Impossible de lire {path}")
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    fps = 30.0
    if video and video.get("avg_frame_rate", "0/0") != "0/0":
        num, den = video["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    return MediaSource(
        path=path,
        duration=round(float(info["format"]["duration"]) * US_PER_SECOND),
        has_video=video is not None,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=fps,
    )


def load_audio(path: str, sample_rate: int = ANALYSIS_RATE) -> np.ndarray:
    """Piste audio décodée en mono float32 [-1, 1]."""
    out = subprocess.run(
        [_require("ffmpeg"), "-v", "error", "-i", path, "-vn", "-ac", "1",
         "-ar", str(sample_rate), "-f", "f32le", "-"],
        capture_output=True,
    )
    if out.returncode != 0:
        raise FFmpegError(out.stderr.decode(errors="replace").strip())
    return np.frombuffer(out.stdout, dtype=np.float32)


def compute_peaks(samples: np.ndarray, sample_rate: int, rate: int = PEAKS_RATE) -> np.ndarray:
    """Forme d'onde réduite pour l'affichage : niveau 0..1 sur une échelle en dB
    (-60 dB -> 0), ce qui rend la parole lisible comme dans CapCut."""
    hop = max(1, sample_rate // rate)
    n = -(-len(samples) // hop)
    padded = np.zeros(n * hop, dtype=np.float32)
    padded[: len(samples)] = np.abs(samples)
    peak = padded.reshape(n, hop).max(axis=1) if n else np.zeros(0, dtype=np.float32)
    db = 20 * np.log10(np.maximum(peak, 1e-6))
    return np.clip((db + 60) / 60, 0, 1).astype(np.float32)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def _clip_filters(clip: Clip, input_index: int, label: int, has_video: bool, has_audio: bool) -> list[str]:
    a = clip.source_in / US_PER_SECOND
    b = clip.source_out / US_PER_SECOND
    parts = []
    if has_video:
        parts.append(f"[{input_index}:v]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{label}]")
    if has_audio:
        parts.append(f"[{input_index}:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[a{label}]")
    return parts


def build_export_command(timeline: Timeline, output: str, track: Track | None = None,
                         filter_script: str = "filter.txt") -> tuple[list[str], str]:
    """Commande ffmpeg qui rend la piste (principale par défaut) bout à bout.

    Renvoie (arguments, contenu du script de filtre). Le graphe de filtres va
    dans un fichier : avec des centaines de coupes il dépasserait la longueur
    maximale d'une ligne de commande.
    Les vides éventuels (magnétisme désactivé) ne sont pas rendus.
    """
    track = track or timeline.main_track
    clips = sorted(track.clips, key=lambda c: c.start)
    if not clips:
        raise ValueError("La piste est vide")
    source_ids = list(dict.fromkeys(c.source_id for c in clips))
    sources = [timeline.sources[s] for s in source_ids]
    has_video = all(s.has_video for s in sources)
    has_audio = all(s.has_audio for s in sources)

    lines: list[str] = []
    for i, clip in enumerate(clips):
        lines += _clip_filters(clip, source_ids.index(clip.source_id), i, has_video, has_audio)
    pads = "".join((f"[v{i}]" if has_video else "") + (f"[a{i}]" if has_audio else "") for i in range(len(clips)))
    outs = ("[outv]" if has_video else "") + ("[outa]" if has_audio else "")
    lines.append(f"{pads}concat=n={len(clips)}:v={int(has_video)}:a={int(has_audio)}{outs}")
    script = ";\n".join(lines)

    args = [_require("ffmpeg"), "-y"]
    for s in sources:
        args += ["-i", s.path]
    args += ["-filter_complex_script", filter_script]
    if has_video:
        args += ["-map", "[outv]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if has_audio:
        args += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    args.append(output)
    return args, script


def export(timeline: Timeline, output: str, track: Track | None = None, on_line=None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        script_path = os.path.join(tmp, "filter.txt")
        args, script = build_export_command(timeline, output, track, script_path)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)
        proc = subprocess.Popen(args, stderr=subprocess.PIPE, text=True, errors="replace")
        tail: list[str] = []
        for line in proc.stderr:
            tail = (tail + [line])[-20:]
            if on_line:
                on_line(line)
        if proc.wait() != 0:
            raise FFmpegError("".join(tail))
