import numpy as np

from ldmv.core.silence import SilenceParams, detect_silences, subtract

SR = 16_000


def tone(seconds, amp=0.5):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(SR * seconds), dtype=np.float32)


def test_detects_silence_between_speech_with_padding():
    audio = np.concatenate([tone(1), silence(1), tone(1)])
    result = detect_silences(audio, SR, SilenceParams(padding_ms=100))
    assert result == [(1_100_000, 1_900_000)]


def test_ignores_short_silences():
    audio = np.concatenate([tone(1), silence(0.3), tone(1)])
    assert detect_silences(audio, SR, SilenceParams(min_silence_ms=500)) == []


def test_no_padding_at_file_edges():
    audio = np.concatenate([silence(1), tone(1), silence(1)])
    result = detect_silences(audio, SR, SilenceParams(padding_ms=100))
    assert result == [(0, 900_000), (2_100_000, 3_000_000)]


def test_short_click_inside_silence_is_ignored():
    audio = np.concatenate([tone(1), silence(0.5), tone(0.02), silence(0.5), tone(1)])
    result = detect_silences(audio, SR, SilenceParams(padding_ms=0, min_sound_ms=60))
    assert len(result) == 1
    a, b = result[0]
    assert a == 1_000_000 and b == 2_020_000


def test_threshold():
    quiet = tone(1, amp=0.001)  # ~ -63 dBFS
    audio = np.concatenate([tone(1), quiet, tone(1)])
    assert detect_silences(audio, SR, SilenceParams(threshold_db=-40, padding_ms=0)) == [(1_000_000, 2_000_000)]
    assert detect_silences(audio, SR, SilenceParams(threshold_db=-70, padding_ms=0)) == []


def test_subtract():
    assert subtract((0, 100), [(10, 20), (50, 60)]) == [(0, 10), (20, 50), (60, 100)]
    assert subtract((30, 55), [(10, 40), (50, 60)]) == [(40, 50)]
    assert subtract((0, 10), [(0, 10)]) == []
