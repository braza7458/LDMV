"""Lecture des médias (ffprobe / ffmpeg) et export de la timeline."""

from __future__ import annotations

import re
import os
import shutil
import subprocess
import tempfile

import numpy as np

from ldmv.core.model import Clip, MediaSource, Timeline, Track
from ldmv.core.timecode import US_PER_SECOND

ANALYSIS_RATE = 16_000  # Hz : largement suffisant pour détecter la parole


class FFmpegError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    """ffmpeg du système s'il existe, sinon celui fourni par le paquet pip
    `imageio-ffmpeg` : l'utilisateur n'a rien à installer à la main."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise FFmpegError("ffmpeg introuvable : lancez `pip install imageio-ffmpeg` ou installez ffmpeg")


_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #.*?Video:.*?(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"Stream #.*?Video:.*?([\d.]+) fps")
_AUDIO = re.compile(r"Stream #.*?Audio:")


def probe(path: str) -> MediaSource:
    """Lit durée et flux en analysant la sortie de `ffmpeg -i` (ffprobe n'est
    pas fourni par imageio-ffmpeg)."""
    out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path],
                         capture_output=True, text=True, errors="replace")
    info = out.stderr
    duration = _DURATION.search(info)
    if not duration:
        raise FFmpegError(info.strip().splitlines()[-1] if info.strip() else f"Impossible de lire {path}")
    h, m, s = duration.groups()
    video = _VIDEO.search(info)
    fps = _FPS.search(info)
    return MediaSource(
        path=path,
        duration=round((int(h) * 3600 + int(m) * 60 + float(s)) * US_PER_SECOND),
        has_video=video is not None,
        has_audio=_AUDIO.search(info) is not None,
        width=int(video.group(1)) if video else 0,
        height=int(video.group(2)) if video else 0,
        fps=float(fps.group(1)) if fps else 30.0,
    )


def load_audio(path: str, sample_rate: int = ANALYSIS_RATE) -> np.ndarray:
    """Piste audio décodée en mono float32 [-1, 1]."""
    out = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", path, "-vn", "-ac", "1",
         "-ar", str(sample_rate), "-f", "f32le", "-"],
        capture_output=True,
    )
    if out.returncode != 0:
        raise FFmpegError(out.stderr.decode(errors="replace").strip())
    return np.frombuffer(out.stdout, dtype=np.float32)


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

    args = [ffmpeg_exe(), "-y"]
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
