"""Pure audio helpers for the harness: 16-bit mono PCM, WAV I/O, and speech segmenting."""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import Iterable
from pathlib import Path


def rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return math.sqrt(sum(s * s for s in samples) / n)


def pcm_duration(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / 2 / sample_rate


def read_wav(data: bytes) -> tuple[bytes, int]:
    """Return (16-bit mono PCM, sample rate). Stereo is averaged down to mono."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV is supported")
        channels = w.getnchannels()
        sr = w.getframerate()
        frames = w.readframes(w.getnframes())
    if channels == 1:
        return frames, sr
    n = len(frames) // 2
    samples = struct.unpack(f"<{n}h", frames)
    mono = [sum(samples[i : i + channels]) // channels for i in range(0, n, channels)]
    return struct.pack(f"<{len(mono)}h", *mono), sr


def write_wav(path: Path | str, pcm: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def find_speech_segments(
    chunks: Iterable[tuple[float, bytes]],
    *,
    threshold: float = 500.0,
    min_gap_secs: float = 0.5,
    sample_rate: int = 16000,
) -> list[tuple[float, float]]:
    """Group timestamped PCM chunks into (start, end) speech segments by RMS energy.

    Each chunk is (wall-clock start time, pcm). Loud chunks closer than min_gap_secs
    are merged into one segment.
    """
    segments: list[list[float]] = []
    for ts, pcm in chunks:
        if rms(pcm) < threshold:
            continue
        end = ts + pcm_duration(pcm, sample_rate)
        if segments and ts - segments[-1][1] < min_gap_secs - 1e-9:
            segments[-1][1] = end
        else:
            segments.append([ts, end])
    return [(s, e) for s, e in segments]


def trailing_silence(pcm: bytes, sample_rate: int, threshold: float = 300.0) -> float:
    """Seconds of quiet after the last speech in `pcm` (0 when there is no speech).

    Subtracted from a clip's playback end to get when the voice actually stopped."""
    chunk = sample_rate * 2 * 20 // 1000
    chunks = [(i / 2 / sample_rate, pcm[i : i + chunk]) for i in range(0, len(pcm), chunk)]
    segments = find_speech_segments(chunks, threshold=threshold, sample_rate=sample_rate)
    return pcm_duration(pcm, sample_rate) - segments[-1][1] if segments else 0.0
