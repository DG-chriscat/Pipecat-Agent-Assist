"""TTS read back test (P5-5, PRD IN-11): does a voice say IDs, dates, amounts, and claim
numbers so a listener hears them right?

Each line is synthesized over the same websocket the bot uses (/v1/speak for Aura-2,
/v2/speak for Flux), timed from sending the text to the first audio chunk on an open
connection (time to first audio, TTFA), then transcribed by Deepgram nova-3 prerecorded
with smart_format. A key entity counts as right when it is in the transcript exactly
(`MATCHERS`). The same STT hears every voice, so STT mistakes cost each voice the same.

The identity read back and the hold update are the bot's real lines (intake.READBACK_LINE
with speak_member_id, hold.hold_line). The others are the kind of line the LLM speaks
in the read back node, written the way an LLM writes them (digits, $, 2:00 PM).
P5-10: member IDs are 6 digits (418302), claim numbers 5 digits (70412).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import statistics
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import httpx
from agent_assist_bot.hold import hold_line
from agent_assist_bot.intake import READBACK_LINE, speak_member_id

SAMPLE_RATE = 16000
LISTEN_URL = "https://api.deepgram.com/v1/listen"
SPEAK_WS = {"aura-2": "wss://api.deepgram.com/v1/speak", "flux": "wss://api.deepgram.com/v2/speak"}


@dataclass(frozen=True)
class Entity:
    kind: str
    said: str  # as written in the line, for the report


@dataclass(frozen=True)
class Line:
    key: str
    text: str
    entities: tuple[Entity, ...]


LINES: tuple[Line, ...] = (
    Line(
        "identity_readback",
        READBACK_LINE.format(name="Maria Lopez", member_id=speak_member_id("418302")),
        (Entity("member_id", "418302 in groups"),),
    ),
    Line(
        "summary_readback",
        "So that's Maria Lopez, date of birth March 12, 1984, calling about claim "
        "70412 for $1,240, which was denied. Is that right?",
        (
            Entity("dob", "March 12, 1984"),
            Entity("claim_number", "70412"),
            Entity("amount", "$1,240"),
        ),
    ),
    Line(
        "callback",
        "A person will call you back at 2:00 PM today, and the corrected bill goes to "
        "8 Oak Street.",
        (Entity("time", "2:00 PM"), Entity("address", "8 Oak Street")),
    ),
    Line(
        "appeal_timing",
        "Once the appeal is filed, a decision usually takes 7 business days.",
        (Entity("business_days", "7 business days"),),
    ),
    Line("hold_update", hold_line(2), (Entity("place", "number 2 in line"),)),
    Line(
        "raw_formats",
        "Your member ID is 418302 and your date of birth is 03/12/1984.",
        (Entity("member_id", "418302 as written"), Entity("dob", "03/12/1984")),
    ),
)

# P5-8 (AP-22): the same lines plus one a customer heard garbled on Flux at speed 1.0
# and 1.1 ("Thanks, you are verified.").
CONTROL_LINES: tuple[Line, ...] = (
    *LINES,
    Line("verified", "Thanks, you are verified.", (Entity("verified", "you are verified"),)),
)


@dataclass(frozen=True)
class FluxSetting:
    """One Flux voice control setting the read back test runs (P5-8)."""

    label: str
    speed: float
    expressivity: int
    voice: str = "flux-alexis-en"


# Speeds at the default expressivity, then the expressivity extremes at speed 1.0.
FLUX_CONTROL_SETTINGS: tuple[FluxSetting, ...] = (
    *(FluxSetting(f"speed {s}", s, 0) for s in (0.8, 0.9, 1.0, 1.1, 1.2)),
    *(FluxSetting(f"expressivity {e}", 1.0, e) for e in (-2, 2)),
)

# ---- normalising what was heard ---------------------------------------------------------

_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine".split())}
_TEENS = {
    w: 10 + i
    for i, w in enumerate(
        "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_TENS = {
    w: 10 * (i + 2)
    for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())
}
_ORD = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
    "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30,
}  # fmt: skip
_NUMBER_WORDS = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_ORD) | {"hundred", "thousand"}


def _numbers(run: list[str]) -> list[int]:
    """Number words to numbers: digit by digit ("one zero zero" -> 1, 0, 0), compound
    ("one thousand two hundred forty" -> 1240), and years ("nineteen eighty four" ->
    19, 84)."""
    out: list[int] = []
    total = current = 0
    last: str | None = None

    def flush() -> None:
        nonlocal total, current, last
        if last is not None:
            out.append(total + current)
        total = current = 0
        last = None

    for w in run:
        n = _UNITS[w] if w in _UNITS else _TEENS.get(w) or _TENS.get(w) or _ORD.get(w, 0)
        if w in ("hundred", "thousand"):
            pass
        elif n < 10:
            if last in ("unit", "teen"):
                flush()
            current += n
            last = "unit"
            continue
        elif n < 20:
            if last in ("unit", "teen", "tens"):
                flush()
            current += n
            last = "teen"
            continue
        else:
            if last in ("unit", "teen", "tens"):
                flush()
            current += n
            last = "tens"
            continue
        if w == "hundred":
            current = (current or 1) * 100
            last = "hundred"
        else:
            total += (current or 1) * 1000
            current = 0
            last = "thousand"
    flush()
    return out


def normalize(text: str) -> str:
    """Lowercase, with number words and ordinals turned into digits. Each spoken number is
    its own token ("twelve forty" -> "12 40"); punctuation is kept."""
    t = (text or "").lower().replace("-", " ")
    # A spoken "dash" is the hyphen.
    t = re.sub(r"\bdash\b", " ", t)
    t = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", t)
    words = re.findall(r"[a-z]+|\d+|[^\sa-z\d]", t)
    out: list[str] = []
    run: list[str] = []
    for i, w in enumerate([*words, ""]):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in _NUMBER_WORDS or (w == "and" and run and nxt in _NUMBER_WORDS):
            if w != "and":
                run.append(w)
            continue
        if run:
            out.extend(str(n) for n in _numbers(run))
            run = []
        out.append(w)
    return " ".join(x for x in out if x)


def collapse(text: str) -> str:
    """Only letters and digits of normalize(): "four one eight, three" -> "4183"."""
    return re.sub(r"[^a-z0-9]", "", normalize(text))


def _has(pattern: str) -> Callable[[str], bool]:
    rx = re.compile(pattern)
    return lambda heard: bool(rx.search(collapse(heard)))


def _amount(heard: str) -> bool:
    """$1,240 as one number: "$12.40" or "twelve forty" is a different amount."""
    t = normalize(heard)  # "$1,240" -> "$ 1 , 240"; "twelve forty" -> "12 40"
    t = re.sub(r"(?<=\d) , (?=\d{3}(?!\d))", "", t)
    t = re.sub(r"(?<=\d) \. (?=\d)", ".", t)
    return bool(re.search(r"(?<![\d.])1240(\.00)?(?![\d.])", t))


MATCHERS: dict[str, Callable[[str], bool]] = {
    "member_id": _has(r"(?<!\d)418302(?!\d)"),
    "dob": _has(r"march121984|(?<!\d)0?3121984(?!\d)"),
    "claim_number": _has(r"(?<!\d)70412(?!\d)"),
    "amount": _amount,
    "time": _has(r"(?<!\d)2(00)?pm"),
    "address": _has(r"(?<!\d)8oakst"),
    "business_days": _has(r"(?<!\d)7businessdays"),
    "place": _has(r"number2inline"),
    "verified": _has(r"you(a)?reverified"),
}

# ---- results ------------------------------------------------------------------------------


@dataclass
class LineResult:
    voice: str
    line: str
    run: int
    heard: str
    ttfa_s: float
    entities: dict[str, bool] = field(default_factory=dict)
    # Seconds of audio (P5-8: shows a speed setting took effect). 0 when not recorded.
    audio_s: float = 0.0


def score(line: Line, heard: str) -> dict[str, bool]:
    return {e.kind: MATCHERS[e.kind](heard) for e in line.entities}


def rescore(results: list[LineResult]) -> list[LineResult]:
    """Score saved results again (after a matcher fix) without new audio."""
    lines = {line.key: line for line in CONTROL_LINES}
    return [
        LineResult(
            r.voice, r.line, r.run, r.heard, r.ttfa_s, score(lines[r.line], r.heard), r.audio_s
        )
        for r in results
    ]


def from_json(raw: str) -> list[LineResult]:
    return [LineResult(**d) for d in json.loads(raw)]


def summarize(results: list[LineResult]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for voice in dict.fromkeys(r.voice for r in results):
        rs = [r for r in results if r.voice == voice]
        marks = [(r.line, k, ok) for r in rs for k, ok in r.entities.items()]
        misses: dict[str, int] = {}
        for line, kind, ok in marks:
            if not ok:
                misses[f"{line}:{kind}"] = misses.get(f"{line}:{kind}", 0) + 1
        out[voice] = {
            "correct": sum(ok for *_, ok in marks),
            "total": len(marks),
            "ttfa_median": statistics.median(r.ttfa_s for r in rs),
            "ttfa_by_line": {
                line: statistics.median(r.ttfa_s for r in rs if r.line == line)
                for line in dict.fromkeys(r.line for r in rs)
            },
            "misses": misses,
        }
    return out


def markdown_table(summary: dict[str, dict]) -> str:
    rows = [
        "| Voice | Entities right | TTFA median (s) | Misses |",
        "| --- | --- | --- | --- |",
    ]
    for voice, s in summary.items():
        pct = round(100 * s["correct"] / s["total"]) if s["total"] else 0
        misses = ", ".join(f"{k} x{n}" for k, n in s["misses"].items()) or "none"
        rows.append(
            f"| {voice} | {s['correct']} / {s['total']} ({pct}%) | "
            f"{s['ttfa_median']:.3f} | {misses} |"
        )
    return "\n".join(rows)


# ---- live: synthesize and transcribe ------------------------------------------------------


def model_of(voice: str) -> str:
    return "flux" if voice.startswith("flux-") else "aura-2"


def speak_url(voice: str, *, speed: float | None = None, expressivity: int | None = None) -> str:
    """The speak websocket URL. Flux takes speed and expressivity as query parameters
    (as DeepgramFluxTTSService sends them at connect)."""
    model = model_of(voice)
    url = f"{SPEAK_WS[model]}?model={voice}&encoding=linear16&sample_rate={SAMPLE_RATE}"
    if speed is not None:
        url += f"&speed={speed}"
    if expressivity is not None:
        url += f"&expressivity={expressivity}"
    return url


async def speak(
    voice: str,
    text: str,
    *,
    api_key: str,
    speed: float | None = None,
    expressivity: int | None = None,
) -> tuple[bytes, float]:
    """PCM (16 kHz linear16) and seconds from sending the text to the first audio, on
    an already open websocket (as in a call, where the connection is up)."""
    from websockets.asyncio.client import connect

    model = model_of(voice)
    url = speak_url(voice, speed=speed, expressivity=expressivity)
    done = "SpeechMetadata" if model == "flux" else "Flushed"
    audio = bytearray()
    first: float | None = None
    async with connect(url, additional_headers={"Authorization": f"Token {api_key}"}) as ws:
        if model == "flux":
            # Flux says Connected first; wait for it so the timing starts on a ready socket.
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert msg.get("type") == "Connected", msg
        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "Speak", "text": text}))
        await ws.send(json.dumps({"type": "Flush"}))
        while True:
            msg = await asyncio.wait_for(ws.recv(), 20)
            if isinstance(msg, bytes):
                if first is None:
                    first = time.perf_counter() - t0
                audio.extend(msg)
                continue
            data = json.loads(msg)
            if data.get("type") == "Error":
                raise RuntimeError(f"{voice}: {data.get('code')} {data.get('description')}")
            if data.get("type") == done:
                break
    if first is None:
        raise RuntimeError(f"{voice}: no audio")
    return bytes(audio), first


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def transcribe(pcm: bytes, *, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            LISTEN_URL,
            params={"model": "nova-3", "smart_format": "true", "language": "en"},
            headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
            content=wav_bytes(pcm),
        )
    r.raise_for_status()
    alt = r.json()["results"]["channels"][0]["alternatives"][0]
    return alt.get("transcript", "")


async def run(
    voices: list[str], runs: int = 3, api_key: str | None = None, keep_audio: str | None = None
) -> list[LineResult]:
    key = api_key or os.environ["DEEPGRAM_API_KEY"]
    results: list[LineResult] = []
    for voice in voices:
        for line in LINES:
            for i in range(runs):
                pcm, ttfa = await speak(voice, line.text, api_key=key)
                heard = await transcribe(pcm, api_key=key)
                if keep_audio and i == 0:
                    os.makedirs(keep_audio, exist_ok=True)
                    with open(os.path.join(keep_audio, f"{voice}-{line.key}.wav"), "wb") as f:
                        f.write(wav_bytes(pcm))
                results.append(LineResult(voice, line.key, i, heard, ttfa, score(line, heard)))
    return results


async def run_flux_controls(
    settings: tuple[FluxSetting, ...] = FLUX_CONTROL_SETTINGS,
    runs: int = 3,
    api_key: str | None = None,
    keep_audio: str | None = None,
) -> list[LineResult]:
    """CONTROL_LINES on each Flux setting (P5-8). `LineResult.voice` is the label."""
    key = api_key or os.environ["DEEPGRAM_API_KEY"]
    results: list[LineResult] = []
    for s in settings:
        for line in CONTROL_LINES:
            for i in range(runs):
                pcm, ttfa = await speak(
                    s.voice, line.text, api_key=key, speed=s.speed, expressivity=s.expressivity
                )
                heard = await transcribe(pcm, api_key=key)
                if keep_audio and i == 0:
                    os.makedirs(keep_audio, exist_ok=True)
                    name = s.label.replace(" ", "_")
                    with open(os.path.join(keep_audio, f"{name}-{line.key}.wav"), "wb") as f:
                        f.write(wav_bytes(pcm))
                results.append(
                    LineResult(
                        s.label,
                        line.key,
                        i,
                        heard,
                        ttfa,
                        score(line, heard),
                        audio_s=len(pcm) / (2 * SAMPLE_RATE),
                    )
                )
    return results


def to_json(results: list[LineResult]) -> str:
    return json.dumps([asdict(r) for r in results], indent=1)
