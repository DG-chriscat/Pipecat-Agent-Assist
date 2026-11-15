"""Live S2 test: one listen only bot transcribes caller and agent on separate tracks.

Run: uv run pytest -m live bot/tests/test_live_s2.py -s
Needs DAILY_API_KEY and DEEPGRAM_API_KEY. Set S2_RESULT=/path.json to keep the results.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from agent_assist_bot.daily_rooms import DailyRoomsClient
from agent_assist_bot.listen import LINE_PREFIX, LISTENER_NAME
from harness.audio import find_speech_segments
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.procstats import ProcSampler
from harness.tts import synthesize

pytestmark = pytest.mark.live

ROOM_TTL_SECS = 540  # under 10 minutes
VOICES = {"caller": "aura-2-arcas-en", "agent": "aura-2-thalia-en"}
GAP_SECS = 1.5
OVERLAP_SECS = 1.0
TAIL_SECS = 5.0

# (role, text). Vocabulary differs between roles so cross talk would show up as
# words from the wrong script. "overlap" marks the turn the caller starts
# OVERLAP_SECS before the agent's previous turn ends.
SCRIPT: list[tuple[str, str, bool]] = [
    ("caller", "Hi, I am calling about hail damage on my roof.", False),
    ("agent", "Thanks for waiting. Can I have your last name please?", False),
    ("caller", "Sure, it is Harrington, spelled the usual way.", False),
    ("agent", "Got it. I see the claim was opened last Tuesday.", False),
    ("caller", "Right, and the adjuster never called me back.", False),
    ("agent", "I am sorry about that. Let me schedule an inspection for Friday morning.", False),
    ("caller", "Friday does not work for me, I travel that weekend.", True),
    ("agent", "No problem, how about Monday instead?", False),
    ("caller", "Monday is perfect, thank you so much.", False),
]


@dataclass
class Spoken:
    role: str
    text: str
    start: float
    end: float
    overlap: bool
    trailing_silence: float = 0.0

    @property
    def speech_end(self) -> float:
        """Wall time the last voiced audio was sent (playback end minus trailing silence)."""
        return self.end - self.trailing_silence


def _trailing_silence(pcm: bytes) -> float:
    chunk = SAMPLE_RATE * 2 * 20 // 1000
    chunks = [(i / 2 / SAMPLE_RATE, pcm[i : i + chunk]) for i in range(0, len(pcm), chunk)]
    segments = find_speech_segments(chunks, threshold=300.0, sample_rate=SAMPLE_RATE)
    return len(pcm) / 2 / SAMPLE_RATE - segments[-1][1] if segments else 0.0


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def _require_env():
    missing = [k for k in ("DAILY_API_KEY", "DEEPGRAM_API_KEY") if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


async def _start_bot(room_url: str):
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agent_assist_bot.spike_s2",
        "--room-url",
        room_url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    log: list[str] = []
    joined = asyncio.Event()

    async def pump():
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            log.append(line)
            if f"Joined {room_url}" in line:
                joined.set()

    asyncio.create_task(pump())
    await asyncio.wait_for(joined.wait(), 60)
    return proc, log


async def _stop_bot(proc) -> None:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except TimeoutError:
            proc.kill()
            await proc.wait()


async def _wait_log(log: list[str], needle: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(needle in line for line in log):
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"bot never logged {needle!r}")


def _lines(log: list[str]) -> list[dict]:
    return [json.loads(x.split(LINE_PREFIX, 1)[1]) for x in log if LINE_PREFIX in x]


def _attribute(lines: list[dict], spoken: list[Spoken]) -> list[tuple[dict, Spoken | None]]:
    """Match each final line to the utterance it most likely came from.

    Candidates are utterances playing around the line's arrival time; the one sharing
    the largest fraction of the line's words wins (any shared word counts, so an STT
    misrecognition still attributes). Only content and time are used, never
    speaker_role, so the role check below is independent.
    """
    out = []
    for line in lines:
        words = _words(line["text"])
        best, best_score = None, 0.0
        for s in spoken:
            if not (s.start - 0.5 <= line["ts"] <= s.end + 3):
                continue
            score = len(words & _words(s.text)) / max(len(words), 1)
            if score > best_score:
                best, best_score = s, score
        out.append((line, best if best_score > 0 else None))
    return out


async def test_per_participant_transcription(tmp_path: Path):
    _require_env()
    audio = {}
    for role, text, _ in SCRIPT:
        audio[text] = await synthesize(text, sample_rate=SAMPLE_RATE, voice=VOICES[role])

    async with DailyRoomsClient() as rooms, rooms.temporary_room(ttl_secs=ROOM_TTL_SECS) as room:
        print(f"\nroom: {room.url} (expires in {ROOM_TTL_SECS}s, deleted at end)")
        proc, log = await _start_bot(room.url)
        sampler = ProcSampler(proc.pid)
        sampler_task = asyncio.create_task(sampler.run(0.5))
        spoken: list[Spoken] = []
        try:
            async with (
                HarnessParticipant("caller") as caller,
                HarnessParticipant("agent") as agent,
            ):
                await caller.join(room.url)
                await agent.join(room.url)
                ids = {
                    "caller": (await agent.wait_for_participant("caller")).id,
                    "agent": (await caller.wait_for_participant("agent")).id,
                }
                await caller.wait_for_participant(LISTENER_NAME)
                await _wait_log(log, "role=caller")
                await _wait_log(log, "role=agent")
                clients = {"caller": caller, "agent": agent}

                # Idle baseline: bot with 2 participants connected, nobody talking.
                idle_start = time.time()
                await asyncio.sleep(5)
                idle_end = time.time()

                talk_start = time.time()
                still_playing: asyncio.Task | None = None
                for i, (role, text, overlap) in enumerate(SCRIPT):
                    if overlap:
                        # Previous turn is still playing; cut in OVERLAP_SECS before its
                        # last voiced audio.
                        prev = spoken[-1]
                        prev_dur = len(audio[prev.text]) / 2 / SAMPLE_RATE
                        prev_speech_end = prev.start + prev_dur - prev.trailing_silence
                        await asyncio.sleep(max(prev_speech_end - OVERLAP_SECS - time.time(), 0))
                    silence = _trailing_silence(audio[text])
                    s = Spoken(role, text, time.time(), 0.0, overlap, silence)
                    spoken.append(s)

                    async def _play(s=s, pcm=audio[text], client=clients[role]):
                        s.start, s.end = await client.play(pcm)

                    task = asyncio.create_task(_play())
                    next_overlaps = i + 1 < len(SCRIPT) and SCRIPT[i + 1][2]
                    if next_overlaps:
                        still_playing = task
                        continue
                    await task
                    if still_playing:
                        await still_playing
                        still_playing = None
                    await asyncio.sleep(GAP_SECS)
                await asyncio.sleep(TAIL_SECS)
                talk_end = time.time()
        finally:
            await asyncio.sleep(1)
            await _stop_bot(proc)
            sampler_task.cancel()

    finals = _lines(log)
    attributed = _attribute(finals, spoken)

    print("\nLabeled transcript (final lines, in arrival order):")
    for line, src in attributed:
        rel = line["ts"] - talk_start
        print(
            f"  +{rel:6.2f}s [{line['speaker_role']:6}] {line['participant_id'][:8]} "
            f"{line['text']!r}  <- {src.role if src else '?'}"
        )

    # Per utterance latency: last voiced audio sent by the harness -> last final line.
    latencies = []
    for s in spoken:
        mine = [ln for ln, src in attributed if src is s]
        if mine:
            latencies.append(
                {
                    "role": s.role,
                    "text": s.text,
                    "overlap": s.overlap,
                    "latency_s": round(max(ln["ts"] for ln in mine) - s.speech_end, 3),
                    "trailing_silence_s": round(s.trailing_silence, 3),
                    "lines": len(mine),
                }
            )
    print("\nLatency, end of speech -> final transcript:")
    for lat in latencies:
        print(f"  {lat['latency_s']:6.3f}s {lat['role']:6} lines={lat['lines']} {lat['text']!r}")

    idle = sampler.summary(idle_start, idle_end)
    talking = sampler.summary(talk_start, talk_end)
    print(f"\nbot CPU/mem idle (2 participants, silent): {idle}")
    print(f"bot CPU/mem talking (2 participants):      {talking}")

    result = {
        "transcript": [
            {**line, "rel_s": round(line["ts"] - talk_start, 3), "source_role": src and src.role}
            for line, src in attributed
        ],
        "spoken": [
            {
                "role": s.role,
                "text": s.text,
                "start_rel_s": round(s.start - talk_start, 3),
                "end_rel_s": round(s.end - talk_start, 3),
                "speech_end_rel_s": round(s.speech_end - talk_start, 3),
                "overlap": s.overlap,
            }
            for s in spoken
        ],
        "latencies": latencies,
        "proc_idle": idle,
        "proc_talking": talking,
        "participant_ids": ids,
    }
    out = os.getenv("S2_RESULT")
    if out:
        Path(out).write_text(json.dumps(result, indent=2))
        Path(out).with_suffix(".log").write_text("\n".join(log))

    # 1. Every final line carries the role of the participant whose track it came from,
    #    and its words come from that role's script (no cross talk).
    assert finals, "no transcript lines"
    for line, src in attributed:
        expected = "caller" if line["participant_id"] == ids["caller"] else "agent"
        assert line["participant_id"] in ids.values(), line
        assert line["speaker_role"] == expected, line
        assert src is not None, f"line matches no utterance: {line}"
        assert src.role == line["speaker_role"], f"cross talk: {line} came from {src}"

    # 2. Every utterance was transcribed.
    missing = [s.text for s in spoken if not any(src is s for _, src in attributed)]
    assert not missing, f"never transcribed: {missing}"

    # 3. Overlap: both the interrupted agent turn and the caller's cut-in got their own lines.
    ov = next(i for i, s in enumerate(spoken) if s.overlap)
    for s in (spoken[ov - 1], spoken[ov]):
        assert any(src is s and ln["speaker_role"] == s.role for ln, src in attributed), s
