"""Render the bundled hold music loop (QH-5): our own track, no licensing risk.

    uv run python -m agent_assist_bot.hold_music_gen [out.wav]

Writes `assets/hold_music.wav` by default. A calm ambient pad: four soft chords
(Dmaj7, Bm7, Gmaj7, A6), 10 s each, no drums, no melody, no vocals. 16 kHz mono,
16-bit, 40 s, normalized to -20 LUFS (BS.1770) so it sits well under speech.

Seamless loop: every note is rendered in one piece and the part that runs past the
end of the loop is folded back onto the start (the last chord fades into the first),
the slow "breathing" swell has a whole number of cycles per loop, and the filter and
the reverb are circular (FFT) over the loop length. So sample N-1 runs straight into
sample 0 with no click or gap.

Deterministic: a fixed seed for the detune and the reverb noise; running it again
gives the same file.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import loudness
import numpy as np

SAMPLE_RATE = 16000
CHORD_SECS = 10.0
# MIDI notes, low to high. Warm voicings in D major; the bass note is the lowest.
CHORDS = (
    (50, 57, 62, 66, 69, 73),  # Dmaj7: D3 A3 D4 F#4 A4 C#5
    (47, 54, 62, 66, 69, 74),  # Bm7:   B2 F#3 D4 F#4 A4 D5
    (43, 50, 59, 62, 66, 69),  # Gmaj7: G2 D3 B3 D4 F#4 A4
    (45, 52, 61, 64, 66, 69),  # A6:    A2 E3 C#4 E4 F#4 A4
)
LOOP_SECS = CHORD_SECS * len(CHORDS)
CROSSFADE_SECS = 4.0  # chords overlap this long, equal power
TARGET_LUFS = -20.0
SEED = 20261116
OUT = Path(__file__).parent / "assets" / "hold_music.wav"


def _hz(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _note(midi: int, n: int, rng: np.random.Generator) -> np.ndarray:
    """One pad voice: three slightly detuned oscillators with soft upper partials."""
    t = np.arange(n) / SAMPLE_RATE
    out = np.zeros(n)
    for cents in (-6.0, 0.0, 6.0):
        f = _hz(midi) * 2 ** ((cents + rng.uniform(-1.5, 1.5)) / 1200)
        phase = rng.uniform(0, 2 * np.pi)
        for k, amp in ((1, 1.0), (2, 0.25), (3, 0.08)):
            out += amp * np.sin(2 * np.pi * f * k * t + phase * k)
    return out / 3


def _envelope(n: int, fade: int) -> np.ndarray:
    """Equal power fade in and out over `fade` samples, flat in between."""
    env = np.ones(n)
    ramp = np.sin(np.linspace(0, np.pi / 2, fade))
    env[:fade] = ramp
    env[-fade:] = ramp[::-1]
    return env


def _shape(x: np.ndarray) -> np.ndarray:
    """Circular tone shaping: roll off the top (warm, nothing harsh on a phone line)
    and the deep lows (no rumble)."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SAMPLE_RATE)
    lowpass = 1 / np.sqrt(1 + (f / 1800) ** 4)
    highpass = 1 / np.sqrt(1 + (70 / np.maximum(f, 1e-6)) ** 4)
    return np.fft.irfft(spec * lowpass * highpass, n=len(x))


def _reverb(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A soft hall: circular convolution with 3 s of decaying noise, so the tail of the
    loop's end rings into its start."""
    n_ir = int(3.0 * SAMPLE_RATE)
    ir = rng.standard_normal(n_ir) * np.exp(-np.arange(n_ir) / (0.7 * SAMPLE_RATE))
    ir /= np.sqrt(np.sum(ir**2))
    padded = np.zeros(len(x))
    padded[:n_ir] = ir
    wet = np.fft.irfft(np.fft.rfft(x) * np.fft.rfft(padded), n=len(x))
    return 0.7 * x + 0.5 * wet


def render() -> np.ndarray:
    """The loop as float samples in [-1, 1], at TARGET_LUFS."""
    rng = np.random.default_rng(SEED)
    n_loop = int(LOOP_SECS * SAMPLE_RATE)
    chord_n = int(CHORD_SECS * SAMPLE_RATE)
    half = int(CROSSFADE_SECS * SAMPLE_RATE) // 2
    loop = np.zeros(n_loop)
    for i, chord in enumerate(CHORDS):
        start = i * chord_n - half  # fades in across the previous chord's end
        n = chord_n + 2 * half
        env = _envelope(n, 2 * half)
        voices = np.zeros(n)
        for j, midi in enumerate(chord):
            gain = 0.6 if j == 0 else 1.0 / (1 + 0.15 * j)  # soft bass, gentle top
            voices += gain * _note(midi, n, rng)
        # Fold onto the loop: whatever runs past either end wraps around.
        idx = (start + np.arange(n)) % n_loop
        np.add.at(loop, idx, voices * env)
    # A slow swell, 4 whole cycles per loop so it is periodic too.
    t = np.arange(n_loop) / SAMPLE_RATE
    loop *= 1 + 0.12 * np.sin(2 * np.pi * 4 * t / LOOP_SECS)
    loop = _reverb(_shape(loop), rng)
    gain_db = TARGET_LUFS - loudness.integrated_loudness(loop, SAMPLE_RATE)
    loop *= 10 ** (gain_db / 20)
    peak = np.max(np.abs(loop))
    if peak > 0.9:  # never clip; a pad this smooth stays far below this
        loop *= 0.9 / peak
    return loop


def write(path: Path, samples: np.ndarray) -> None:
    pcm = np.round(np.clip(samples, -1, 1) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> Path:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0]) if argv else OUT
    samples = render()
    write(out, samples)
    lufs = loudness.integrated_loudness(samples, SAMPLE_RATE)
    print(f"wrote {out}: {LOOP_SECS:.0f} s, {SAMPLE_RATE} Hz mono, {lufs:.1f} LUFS")
    return out


if __name__ == "__main__":
    main()
