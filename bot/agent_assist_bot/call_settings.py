"""Per call bot settings (P5-5, PRD AP-20, IN-11).

The server reads the Settings page values when it hands a call to a bot and sends them
with the assignment: on stdin for a warm bot (`warm.read_assignment`), as JSON in
AGENT_ASSIST_BOT_SETTINGS for a cold one. The bot builds its call from them once, so
a call keeps its settings even if someone saves new ones mid call.

The server validates on save (agent_assist_server.bot_settings); this side only
guards against a value it cannot use (a bad one falls back to its default, logged),
so a call never fails over settings. No settings at all means the defaults:
Flux TTS Alexis, the fixed greeting, INTAKE_PACK, read back on, the hold line every
HOLD_UPDATE_SECS (default 60), Nova-3 STT for intake and for assist (P5-7, IN-14;
the STT services are built in stt.py).

`build_tts` is the TTS factory: DeepgramTTSService (/v1/speak) for Aura-2 and
DeepgramFluxTTSService (/v2/speak) for Flux. Flux voice ids fail on /v1/speak
(V2_MODEL_ON_V1_SPEAK_ENDPOINT), so the model picks the service class, not just the
voice name.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from loguru import logger

from agent_assist_bot.hold import HOLD_INTERVAL_ENV, hold_interval_from_env, place_line
from agent_assist_bot.intake import GREETING
from agent_assist_bot.packs import DEFAULT_PACK, PACKS_DIR
from agent_assist_bot.stt import (
    ASSIST_MODELS,
    DEFAULT_ASSIST_MODEL,
    DEFAULT_INTAKE_MODEL,
    INTAKE_MODELS,
)

SETTINGS_ENV = "AGENT_ASSIST_BOT_SETTINGS"
PACK_ENV = "INTAKE_PACK"

TTS_MODELS = ("aura-2", "flux")
# IN-11: Flux is the default because it read entities at least as well as Aura-2:
# Flux Alexis 80/80, Aura-2 Thalia 79/80 (docs/spikes/tts-flux-vs-aura.md). Same value
# as agent_assist_server.bot_settings.
DEFAULT_TTS_MODEL = "flux"
DEFAULT_VOICES = {"aura-2": "aura-2-thalia-en", "flux": "flux-alexis-en"}
DEFAULT_TTS_VOICE = DEFAULT_VOICES[DEFAULT_TTS_MODEL]
VOICE_PREFIX = {"aura-2": "aura-2-", "flux": "flux-"}
PLACE = "{place}"
DEFAULT_HOLD_TEXT = f"Thanks for waiting. {PLACE} A person will be with you shortly."
MAX_TEXT = 400


def _pack_exists(pack: str) -> bool:
    return isinstance(pack, str) and pack.isidentifier() and (PACKS_DIR / f"{pack}.yaml").is_file()


@dataclass(frozen=True)
class CallSettings:
    tts_model: str
    tts_voice: str
    greeting_text: str
    intake_pack: str
    identity_read_back: bool
    hold_update_text: str
    hold_update_secs: int
    stt_intake_model: str = DEFAULT_INTAKE_MODEL
    stt_assist_model: str = DEFAULT_ASSIST_MODEL

    @classmethod
    def defaults(cls, env: Mapping[str, str] | None = None) -> CallSettings:
        env = os.environ if env is None else env
        pack = (env.get(PACK_ENV) or "").strip() or DEFAULT_PACK
        return cls(
            tts_model=DEFAULT_TTS_MODEL,
            tts_voice=DEFAULT_TTS_VOICE,
            greeting_text=GREETING,
            intake_pack=pack,
            identity_read_back=True,
            hold_update_text=DEFAULT_HOLD_TEXT,
            hold_update_secs=int(hold_interval_from_env(env)),
        )

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any], env: Mapping[str, str] | None = None
    ) -> CallSettings:
        d = cls.defaults(env)
        out: dict[str, Any] = {f.name: getattr(d, f.name) for f in fields(cls)}
        bad: list[str] = []

        def take(key: str, ok) -> None:
            if key not in data:
                return
            if ok(data[key]):
                out[key] = data[key]
            else:
                bad.append(key)

        def text(v: Any) -> bool:
            return isinstance(v, str) and 0 < len(v.strip()) and len(v) <= MAX_TEXT

        take("tts_model", lambda v: v in TTS_MODELS)
        model = out["tts_model"]
        take("tts_voice", lambda v: isinstance(v, str) and v.startswith(VOICE_PREFIX[model]))
        if not out["tts_voice"].startswith(VOICE_PREFIX[model]):
            out["tts_voice"] = DEFAULT_VOICES[model]
        take("greeting_text", text)
        take("intake_pack", _pack_exists)
        take("identity_read_back", lambda v: isinstance(v, bool))
        take("hold_update_text", text)
        take(
            "hold_update_secs",
            lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 3600,
        )
        take("stt_intake_model", lambda v: v in INTAKE_MODELS)
        take("stt_assist_model", lambda v: v in ASSIST_MODELS)
        if bad:
            logger.warning(f"[settings] unusable values for {sorted(bad)}; using defaults")
        return cls(**out)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CallSettings:
        """A cold bot's settings (AGENT_ASSIST_BOT_SETTINGS), else the defaults."""
        env = os.environ if env is None else env
        raw = env.get(SETTINGS_ENV)
        if not raw:
            return cls.defaults(env)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            logger.warning(f"[settings] {SETTINGS_ENV} is not a JSON object; using defaults")
            return cls.defaults(env)
        return cls.from_mapping(data, env)

    def hold_line(self, position: int | None) -> str:
        return self.hold_update_text.replace(PLACE, place_line(position))

    def describe(self) -> str:
        """For the log: what this call runs with, no free text."""
        return (
            f"tts={self.tts_model}/{self.tts_voice} pack={self.intake_pack} "
            f"read_back={'on' if self.identity_read_back else 'off'} "
            f"hold_every={self.hold_update_secs}s "
            f"stt_intake={self.stt_intake_model} stt_assist={self.stt_assist_model}"
        )


def build_tts(settings: CallSettings, *, api_key: str):
    """The call's TTS service: Aura-2 on /v1/speak or Flux on /v2/speak."""
    if settings.tts_model == "flux":
        from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService

        # Pipecat's Flux default: LLM tokens stream straight to Flux, which places the
        # synthesis boundaries itself. Fixed lines arrive whole as TTSSpeakFrames.
        return DeepgramFluxTTSService(
            api_key=api_key,
            settings=DeepgramFluxTTSService.Settings(voice=settings.tts_voice),
        )
    from pipecat.services.deepgram.tts import DeepgramTTSService

    return DeepgramTTSService(
        api_key=api_key, settings=DeepgramTTSService.Settings(voice=settings.tts_voice)
    )


__all__ = ["HOLD_INTERVAL_ENV", "CallSettings", "build_tts"]
