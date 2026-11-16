"""Hold music (QH-5): the mixer that plays it under the bot, its config, and the track."""

import wave
from pathlib import Path

import loudness
import numpy as np
import pytest
from agent_assist_bot import hold_music, hold_music_gen
from pipecat.frames.frames import MixerEnableFrame, MixerUpdateSettingsFrame

RATE = 16000
CHUNK = RATE * 40 // 1000  # 40 ms, the transport's default output chunk
SILENCE = b"\x00" * CHUNK * 2


def tone(amplitude: int = 6000, n: int = CHUNK) -> np.ndarray:
    t = np.arange(n) / RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def speech() -> bytes:
    """A chunk the mixer must treat as the bot talking (TTS level)."""
    return tone(6000).tobytes()


def write_track(path: Path, seconds: float = 2.0, rate: int = RATE, level: float = 0.5) -> None:
    n = int(seconds * rate)
    t = np.arange(n) / rate
    samples = (level * 32767 * np.sin(2 * np.pi * 330 * t)).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def level(pcm: bytes) -> float:
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(a * a)))


async def run(mixer, chunks: int, audio: bytes = SILENCE) -> list[bytes]:
    return [await mixer.mix(audio) for _ in range(chunks)]


@pytest.fixture
def track(tmp_path) -> Path:
    p = tmp_path / "track.wav"
    write_track(p)
    return p


@pytest.fixture
async def mixer(track):
    m = hold_music.HoldMusicMixer(track, volume=1.0)
    await m.start(RATE)
    return m


# ---- the mixer ----------------------------------------------------------------------


async def test_silent_until_enabled(mixer):
    # Intake: the mixer is wired into the transport but plays nothing.
    out = await run(mixer, 50)
    assert all(c == SILENCE for c in out)
    assert not mixer.enabled


async def test_enable_fades_the_music_in(mixer):
    mixer.enable()
    out = await run(mixer, 100)  # 4 s
    assert level(out[0]) < level(out[-1])
    assert level(out[-1]) > 5000  # full level once faded in (track at half scale)
    assert mixer.enabled


async def test_disable_stops_within_one_chunk(mixer):
    mixer.enable()
    await run(mixer, 100)
    mixer.disable()
    out = await run(mixer, 3)
    # The chunk after disable ramps to zero (no click), everything after is silent.
    assert out[1] == SILENCE and out[2] == SILENCE
    assert not mixer.enabled


async def test_music_waits_for_the_line_being_spoken(mixer):
    # Enabled while the handoff line plays: music starts once the bot stops talking.
    mixer.enable()
    talking = await run(mixer, 25, speech())  # 1 s of bot speech
    assert all(c == speech() for c in talking)
    out = await run(mixer, 100)  # bot quiet: hangover, then the fade in
    assert level(out[-1]) > 5000


async def test_music_starts_on_its_own_if_no_line_comes(mixer):
    mixer.enable()
    out = await run(mixer, 100)
    assert level(out[-1]) > 5000


async def test_music_waits_through_tts_first_byte_delay(mixer):
    # `queued` lands before the handoff line's first audio: no music in that gap, so the
    # line is not played over a fade in; music starts after the line.
    mixer.enable()
    gap = await run(mixer, 20)  # 0.8 s before TTS audio arrives
    assert all(c == SILENCE for c in gap)
    await run(mixer, 25, speech())
    after = await run(mixer, 10)  # 0.4 s after the line: still waiting
    assert all(c == SILENCE for c in after)
    out = await run(mixer, 75)
    assert level(out[-1]) > 5000


async def test_enabled_after_the_line_already_ended(mixer):
    # The handoff line finished before `queued` arrived: music comes in on its own.
    await run(mixer, 25, speech())
    await run(mixer, 25)
    mixer.enable()
    out = await run(mixer, 100)
    assert level(out[-1]) > 5000


async def test_bot_speech_ducks_the_music_about_12_db(mixer):
    mixer.enable()
    full = level((await run(mixer, 100))[-1])
    # A hold update: music under it drops by ~12 dB.
    out = await run(mixer, 25, speech())
    music = np.frombuffer(out[-1], dtype=np.int16).astype(np.int32) - tone(6000)
    ducked = level(music.astype(np.int16).tobytes())
    db = 20 * np.log10(ducked / full)
    assert -14 <= db <= -10
    # After the update the music comes back up.
    back = await run(mixer, 50)
    assert level(back[-1]) == pytest.approx(full, rel=0.1)


async def test_volume_scales_the_music(track):
    loud = hold_music.HoldMusicMixer(track, volume=1.0)
    soft = hold_music.HoldMusicMixer(track, volume=0.25)
    for m in (loud, soft):
        await m.start(RATE)
        m.enable()
    a = level((await run(loud, 100))[-1])
    b = level((await run(soft, 100))[-1])
    assert b / a == pytest.approx(0.25, rel=0.05)


async def test_mixer_control_frames(mixer):
    await mixer.process_frame(MixerEnableFrame(enable=True))
    assert mixer.enabled
    await mixer.process_frame(MixerUpdateSettingsFrame(settings={"volume": 0.5}))
    assert mixer.volume == 0.5
    await mixer.process_frame(MixerEnableFrame(enable=False))
    assert not mixer.enabled


async def test_loops_without_a_gap(mixer):
    mixer.enable()
    out = await run(mixer, 200)  # 8 s of a 2 s track
    assert min(level(c) for c in out[100:]) > 5000


async def test_resamples_track_to_the_output_rate(track):
    m = hold_music.HoldMusicMixer(track, volume=1.0)
    await m.start(24000)  # Daily output default
    m.enable()
    chunk = b"\x00" * (24000 * 40 // 1000) * 2
    out = [await m.mix(chunk) for _ in range(100)]
    a = np.frombuffer(out[-1], dtype=np.int16).astype(np.float64)
    peak_hz = np.argmax(np.abs(np.fft.rfft(a))) * 24000 / len(a)
    assert peak_hz == pytest.approx(330, abs=25)  # pitch kept, not sped up


async def test_stereo_track_is_mixed_down(tmp_path):
    p = tmp_path / "stereo.wav"
    n = RATE * 2
    t = np.arange(n) / RATE
    left = (16000 * np.sin(2 * np.pi * 330 * t)).astype(np.int16)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.stack([left, left], axis=1).tobytes())
    m = hold_music.HoldMusicMixer(p, volume=1.0)
    await m.start(RATE)
    m.enable()
    out = await run(m, 100)
    assert level(out[-1]) > 5000


async def test_missing_track_plays_nothing(tmp_path):
    m = hold_music.HoldMusicMixer(tmp_path / "nope.wav", volume=1.0)
    await m.start(RATE)  # logs, never raises: the call goes on without music
    m.enable()
    assert all(c == SILENCE for c in await run(m, 10))


# ---- config -------------------------------------------------------------------------


def test_default_is_on_with_the_bundled_track():
    m = hold_music.hold_music_from_env({})
    assert isinstance(m, hold_music.HoldMusicMixer)
    assert m.path == hold_music.DEFAULT_TRACK
    assert m.volume == 0.35


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
def test_hold_music_off(value):
    assert hold_music.hold_music_from_env({"HOLD_MUSIC": value}) is None


def test_volume_and_path_from_env(track):
    m = hold_music.hold_music_from_env({"HOLD_MUSIC_VOLUME": "0.6", "HOLD_MUSIC_PATH": str(track)})
    assert m.volume == 0.6
    assert m.path == track


@pytest.mark.parametrize(("raw", "expected"), [("2", 1.0), ("-1", 0.0), ("loud", 0.35)])
def test_volume_is_clamped_or_defaulted(raw, expected):
    assert hold_music.hold_music_from_env({"HOLD_MUSIC_VOLUME": raw}).volume == expected


def test_missing_override_path_turns_music_off(tmp_path):
    assert hold_music.hold_music_from_env({"HOLD_MUSIC_PATH": str(tmp_path / "x.wav")}) is None


# ---- the bundled track --------------------------------------------------------------


def test_bundled_track_format():
    path = hold_music.DEFAULT_TRACK
    assert path.stat().st_size < 1.5 * 1024 * 1024
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getsampwidth() == 2
        secs = w.getnframes() / w.getframerate()
    assert 30 <= secs <= 60


def bundled() -> np.ndarray:
    with wave.open(str(hold_music.DEFAULT_TRACK), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768


def test_bundled_track_loudness_about_minus_20_lufs():
    assert loudness.integrated_loudness(bundled(), 16000) == pytest.approx(-20, abs=1)


def test_bundled_track_loops_seamlessly():
    x = bundled()
    # The jump from the last sample back to the first is no bigger than a normal step.
    steps = np.abs(np.diff(x))
    assert abs(x[0] - x[-1]) <= np.percentile(steps, 99.9)


def test_bundled_track_has_no_quiet_gaps():
    x = bundled()
    win = 1600  # 100 ms
    rms = np.sqrt(np.mean(x[: len(x) // win * win].reshape(-1, win) ** 2, axis=1))
    loud = 20 * np.log10(np.median(rms))
    assert 20 * np.log10(rms.min()) > loud - 12  # a pad, never close to silence


def test_generator_renders_the_committed_track(tmp_path):
    # Deterministic: re-running the script gives the WAV in the repo (to within 2 LSB,
    # FFT rounding can differ by CPU).
    out = tmp_path / "hold.wav"
    hold_music_gen.main([str(out)])
    with wave.open(str(out), "rb") as w:
        fresh = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(int)
    committed = (bundled() * 32768).astype(int)
    assert fresh.shape == committed.shape
    assert np.max(np.abs(fresh - committed)) <= 2
