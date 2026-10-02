"""Unités de temps.

Tout le temps est stocké en microsecondes entières (µs) : pas d'erreur
d'arrondi cumulée lors des coupes successives, contrairement aux flottants.
"""

US_PER_SECOND = 1_000_000
US_PER_MS = 1_000


def seconds(value: float) -> int:
    return round(value * US_PER_SECOND)


def ms(value: float) -> int:
    return round(value * US_PER_MS)


def to_seconds(us: int) -> float:
    return us / US_PER_SECOND


def frame_duration(fps: float) -> int:
    return round(US_PER_SECOND / fps)


def snap_to_frame(us: int, fps: float) -> int:
    """Arrondit un temps à l'image la plus proche."""
    frame = US_PER_SECOND / fps
    return round(round(us / frame) * frame)


def format_timecode(us: int, fps: float = 30.0) -> str:
    """Format CapCut : HH:MM:SS:FF."""
    sign = "-" if us < 0 else ""
    us = abs(us)
    total_seconds, rest = divmod(us, US_PER_SECOND)
    frames = int(rest * fps / US_PER_SECOND)
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


def format_ruler(us: int) -> str:
    """Format de la règle : MM:SS."""
    total_seconds = us // US_PER_SECOND
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"
