"""Warm bot mode (P5-2): `python -m agent_assist_bot --warm`.

The server keeps one of these ready (agent_assist_server.bot_launcher.WarmBotPool).
At start it imports the bot and loads what a cold bot loads before it can join a
room: the Anthropic SDK, Silero VAD, the tone reader model (shared per process, so
the call's readers reuse it), the use case packs, and the Daily SDK. Then it prints
READY_LINE on stdout and blocks on stdin for one JSON line naming its call:

    {"room_url": ..., "token": ..., "session_id": ..., "call_id": ..., "call_domain": ...,
     "settings": {...}}

call_id/call_domain are null for a browser caller. `settings` (P5-5) are the bot
settings the server read at assignment (agent_assist_bot.call_settings); absent means
defaults. EOF instead of a line means the server is shutting down: exit 0 without
joining anything.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import IO

from loguru import logger

from agent_assist_bot.tone import TONE_PRECISION, TONE_REVISION, TONE_THREADS, tone_on

READY_LINE = "agent_assist_bot warm ready"
# Pipecat Tone shares a loaded model per process under this key (PipecatToneReader.setup).
TONE_MODEL_KEY = ("reader", TONE_REVISION, TONE_PRECISION, TONE_THREADS)


class AssignmentError(ValueError):
    """The server sent something that is not a call assignment."""


def read_assignment(stream: IO[str]):
    """The call for this warm bot, or None on EOF (server shutting down)."""
    from agent_assist_bot.__main__ import DialinArgs

    raw = stream.readline()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise AssignmentError("assignment is not JSON") from None
    if not isinstance(data, dict):
        raise AssignmentError("assignment is not an object")
    missing = [k for k in ("room_url", "token") if not data.get(k)]
    if missing:
        raise AssignmentError(f"assignment is missing {', '.join(missing)}")
    if bool(data.get("call_id")) != bool(data.get("call_domain")):
        raise AssignmentError("call_id and call_domain must come together")
    settings = data.get("settings")
    if settings is not None and not isinstance(settings, dict):
        raise AssignmentError("settings must be an object")
    return DialinArgs(
        room_url=data["room_url"],
        token=data["token"],
        call_id=data.get("call_id") or None,
        call_domain=data.get("call_domain") or None,
        session_id=data.get("session_id") or "",
        settings=settings,
    )


def _load_llm() -> None:
    # The Anthropic SDK is imported on first use (agent_assist_bot.llm); ~3 s cold.
    import anthropic  # noqa: F401

    import agent_assist_bot.llm_anthropic  # noqa: F401


def _load_vad() -> None:
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    SileroVADAnalyzer()  # onnxruntime and the model file, warmed in this process


def _load_tone() -> None:
    from pipecat.processors.tone._models import _shared
    from pipecat.processors.tone.reader.model import PipecatToneReaderModel

    _shared(
        TONE_MODEL_KEY,
        lambda: PipecatToneReaderModel.download(
            revision=TONE_REVISION, precision=TONE_PRECISION, threads=TONE_THREADS
        ),
    )


def _load_packs() -> None:
    from agent_assist_bot.packs import load_packs

    load_packs()


def _load_daily() -> None:
    from daily import Daily
    from pipecat.transports.daily.transport import DailyTransportClient

    if not DailyTransportClient._daily_initialized:
        DailyTransportClient._daily_initialized = True
        Daily.init()


LOADERS: dict[str, Callable[[], None]] = {
    "llm": _load_llm,
    "vad": _load_vad,
    "tone": _load_tone,
    "packs": _load_packs,
    "daily": _load_daily,
}


def preload(
    env: Mapping[str, str] | None = None,
    loaders: Mapping[str, Callable[[], None]] | None = None,
) -> dict[str, float]:
    """Load everything a call needs before it joins. Returns seconds per step."""
    env = os.environ if env is None else env
    loaders = LOADERS if loaders is None else loaders
    steps: dict[str, float] = {}
    for name, load in loaders.items():
        if name == "tone" and not tone_on(env):
            continue
        started = time.perf_counter()
        load()
        steps[name] = round(time.perf_counter() - started, 3)
    logger.info(f"[warm] preloaded {steps}")
    return steps
