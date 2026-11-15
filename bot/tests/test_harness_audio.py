import io
import struct
import wave

from harness.audio import (
    find_speech_segments,
    pcm_duration,
    read_wav,
    rms,
    write_wav,
)

SR = 16000


def tone(ms: int, amp: int = 8000) -> bytes:
    n = SR * ms // 1000
    return struct.pack(f"<{n}h", *([amp, -amp] * (n // 2)))


def silence(ms: int) -> bytes:
    return b"\x00\x00" * (SR * ms // 1000)


def test_rms_of_silence_is_zero_and_tone_is_amplitude():
    assert rms(silence(20)) == 0
    assert abs(rms(tone(20, 1000)) - 1000) < 1
    assert rms(b"") == 0


def test_pcm_duration():
    assert pcm_duration(silence(500), SR) == 0.5


def test_find_speech_segments_merges_short_gaps_and_splits_long_ones():
    chunks = []
    t = 100.0
    # 0.2s silence, 0.4s speech, 0.1s gap (merged), 0.2s speech, 1.0s silence, 0.3s speech
    for kind, ms in [("s", 200), ("t", 400), ("s", 100), ("t", 200), ("s", 1000), ("t", 300)]:
        for _ in range(ms // 20):
            chunks.append((t, tone(20) if kind == "t" else silence(20)))
            t += 0.02

    segs = find_speech_segments(chunks, threshold=500, min_gap_secs=0.5)

    assert len(segs) == 2
    (s1, e1), (s2, e2) = segs
    assert abs(s1 - 100.2) < 1e-6
    assert abs(e1 - 100.9) < 1e-6
    assert abs(s2 - 101.9) < 1e-6
    assert abs(e2 - 102.2) < 1e-6


def test_find_speech_segments_empty():
    assert find_speech_segments([], threshold=500) == []


def test_wav_round_trip(tmp_path):
    pcm = tone(100)
    path = tmp_path / "x.wav"
    write_wav(path, pcm, SR)
    got, sr = read_wav(path.read_bytes())
    assert sr == SR
    assert got == pcm


def test_read_wav_downmixes_stereo():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack("<4h", 100, 300, -100, -300))
    pcm, sr = read_wav(buf.getvalue())
    assert sr == SR
    assert struct.unpack("<2h", pcm) == (200, -200)
