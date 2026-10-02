"""Coupe des silences en ligne de commande, sans interface.

    ldmv-silence entree.mp4 -o sortie.mp4 --threshold -40 --min-silence 500 --padding 100
    ldmv-silence entree.mp4 --dry-run        # liste les silences sans exporter
"""

from __future__ import annotations

import argparse
import sys

from ldmv.core.edits import Editor
from ldmv.core.silence import SilenceParams, detect_silences, total_duration
from ldmv.core.timecode import format_timecode
from ldmv.media.ffmpeg import ANALYSIS_RATE, FFmpegError, export, load_audio, probe


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Supprime automatiquement les silences d'une vidéo.")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="fichier de sortie (défaut : <entrée>_sans_silences.mp4)")
    p.add_argument("--threshold", type=float, default=-40.0, help="seuil de silence en dB (défaut -40)")
    p.add_argument("--min-silence", type=int, default=500, help="durée minimale d'un silence en ms (défaut 500)")
    p.add_argument("--padding", type=int, default=100, help="marge gardée autour de la parole en ms (défaut 100)")
    p.add_argument("--min-sound", type=int, default=60, help="sons isolés plus courts ignorés, en ms (défaut 60)")
    p.add_argument("--dry-run", action="store_true", help="affiche les silences sans exporter")
    args = p.parse_args(argv)

    params = SilenceParams(args.threshold, args.min_silence, args.padding, args.min_sound)
    try:
        source = probe(args.input)
        if not source.has_audio:
            print("Ce fichier n'a pas de piste audio.", file=sys.stderr)
            return 1
        silences = detect_silences(load_audio(args.input), ANALYSIS_RATE, params)
    except FFmpegError as e:
        print(e, file=sys.stderr)
        return 1

    fps = source.fps
    print(f"{len(silences)} silences détectés, {format_timecode(total_duration(silences), fps)} "
          f"à retirer sur {format_timecode(source.duration, fps)}")
    if args.dry_run:
        for a, b in silences:
            print(f"  {format_timecode(a, fps)} -> {format_timecode(b, fps)}")
        return 0

    editor = Editor()
    editor.append_source(source)
    report = editor.remove_silences({source.id: silences})
    output = args.output or args.input.rsplit(".", 1)[0] + "_sans_silences.mp4"
    print(f"{report.clips_after} segments conservés, export vers {output}…")
    try:
        export(editor.timeline, output)
    except FFmpegError as e:
        print(e, file=sys.stderr)
        return 1
    print("Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
