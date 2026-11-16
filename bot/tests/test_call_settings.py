"""Per call bot settings (P5-5): parsed from the server's assignment, a TTS service built
for Aura-2 or Flux, and the greeting, pack, read back, and hold settings reaching the
intake and hold code. No network."""

from __future__ import annotations

import json

import pytest
from agent_assist_bot import call_settings as cs
from agent_assist_bot import hold, intake
from agent_assist_bot.calls import MemoryCallStore
from agent_assist_bot.packs import load_pack
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService
from pipecat.services.deepgram.tts import DeepgramTTSService

FLUX = {
    "tts_model": "flux",
    "tts_voice": "flux-hannah-en",
    "greeting_text": "Hello. You're talking to an AI assistant, and a person will join after.",
    "intake_pack": "account_payments",
    "identity_read_back": False,
    "hold_update_text": "Still with us. {place}",
    "hold_update_secs": 20,
    "stt_intake_model": "nova-3-general",
    "stt_assist_model": "nova-3-medical",
}


def test_defaults_match_todays_bot():
    d = cs.CallSettings.defaults({})
    assert d.tts_model == cs.DEFAULT_TTS_MODEL and d.tts_voice == cs.DEFAULT_TTS_VOICE
    assert d.greeting_text == intake.GREETING
    assert d.intake_pack == "claims_status"
    assert d.identity_read_back is True
    assert d.hold_update_secs == 60
    assert d.hold_line(1) == hold.hold_line(1)
    assert d.hold_line(4) == hold.hold_line(4)
    assert d.hold_line(None) == hold.hold_line(None)


def test_default_voice_is_flux_alexis_per_the_read_back_test():
    """IN-11, docs/spikes/tts-flux-vs-aura.md: Flux Alexis 80/80 entities, Aura-2
    Thalia 79/80, Flux Hannah 74/80. Flux matched Aura-2, so the best Flux voice is
    the default."""
    d = cs.CallSettings.defaults({})
    assert (d.tts_model, d.tts_voice) == ("flux", "flux-alexis-en")
    assert cs.DEFAULT_VOICES == {"aura-2": "aura-2-thalia-en", "flux": "flux-alexis-en"}


def test_bot_and_server_defaults_agree():
    from agent_assist_server import bot_settings as server

    bot = cs.CallSettings.defaults({})
    assert server.defaults({}) == {
        "tts_model": bot.tts_model,
        "tts_voice": bot.tts_voice,
        "greeting_text": bot.greeting_text,
        "intake_pack": bot.intake_pack,
        "identity_read_back": bot.identity_read_back,
        "hold_update_text": bot.hold_update_text,
        "hold_update_secs": bot.hold_update_secs,
        "stt_intake_model": bot.stt_intake_model,
        "stt_assist_model": bot.stt_assist_model,
        "tts_speed": bot.tts_speed,
        "tts_expressivity": bot.tts_expressivity,
        "slow_readback": bot.slow_readback,
        "readback_speed": bot.readback_speed,
    }


def test_bot_and_server_flux_control_ranges_agree():
    from agent_assist_server import bot_settings as server

    lo, hi = cs.FLUX_SPEED_RANGE
    assert lo <= server.MIN_TTS_SPEED and server.MAX_TTS_SPEED <= hi
    assert cs.READBACK_SPEED_RANGE == (server.MIN_READBACK_SPEED, server.MAX_READBACK_SPEED)


def test_bot_and_server_stt_model_lists_agree():
    from agent_assist_bot import stt
    from agent_assist_server import bot_settings as server

    assert server.STT_INTAKE_MODELS == stt.INTAKE_MODELS
    assert server.STT_ASSIST_MODELS == stt.ASSIST_MODELS


def test_stt_defaults_are_nova_3_on_both_sides():
    d = cs.CallSettings.defaults({})
    assert (d.stt_intake_model, d.stt_assist_model) == ("nova-3-general", "nova-3-general")


@pytest.mark.parametrize(
    "over",
    [
        {"stt_intake_model": "nova-3-medical"},  # no medical intake
        {"stt_intake_model": 7},
        {"stt_assist_model": "flux-general-multi"},
    ],
)
def test_an_unusable_stt_model_falls_back_to_its_default(over):
    s = cs.CallSettings.from_mapping({**FLUX, **over}, env={})
    d = cs.CallSettings.defaults({})
    for key in over:
        assert getattr(s, key) == getattr(d, key)


def test_log_line_names_the_stt_models():
    line = cs.CallSettings.from_mapping(FLUX, env={}).describe()
    assert "stt_intake=nova-3-general" in line and "stt_assist=nova-3-medical" in line


def test_env_knobs_still_set_the_default_pack_and_interval():
    d = cs.CallSettings.defaults({"INTAKE_PACK": "general_question", "HOLD_UPDATE_SECS": "8"})
    assert d.intake_pack == "general_question" and d.hold_update_secs == 8


def test_from_mapping_takes_the_servers_values():
    s = cs.CallSettings.from_mapping(FLUX, env={})
    assert s == cs.CallSettings(**FLUX)


def test_a_bad_value_falls_back_to_its_default_only():
    s = cs.CallSettings.from_mapping(
        {**FLUX, "tts_model": "aura-9", "hold_update_secs": "soon", "surprise": 1}, env={}
    )
    d = cs.CallSettings.defaults({})
    # The model falls back to the default (Flux); the Flux voice still fits it, so stays.
    assert (s.tts_model, s.tts_voice) == (d.tts_model, "flux-hannah-en")
    assert s.hold_update_secs == d.hold_update_secs
    assert s.greeting_text == FLUX["greeting_text"]
    aura = cs.CallSettings.from_mapping({"tts_model": "aura-9", "tts_voice": "aura-2-orion-en"})
    assert (aura.tts_model, aura.tts_voice) == (d.tts_model, d.tts_voice)


def test_voice_from_the_other_model_falls_back_to_the_model_default():
    s = cs.CallSettings.from_mapping({**FLUX, "tts_voice": "aura-2-thalia-en"}, env={})
    assert s.tts_model == "flux" and s.tts_voice == cs.DEFAULT_VOICES["flux"]


def test_unknown_pack_falls_back():
    s = cs.CallSettings.from_mapping({**FLUX, "intake_pack": "nope"}, env={})
    assert s.intake_pack == "claims_status"


def test_from_env_reads_the_cold_bots_json():
    env = {cs.SETTINGS_ENV: json.dumps(FLUX)}
    assert cs.CallSettings.from_env(env).tts_voice == "flux-hannah-en"


@pytest.mark.parametrize("raw", [None, "", "not json", "[1]"])
def test_from_env_without_good_json_is_the_defaults(raw):
    env = {} if raw is None else {cs.SETTINGS_ENV: raw}
    assert cs.CallSettings.from_env(env) == cs.CallSettings.defaults(env)


def test_log_line_names_model_voice_and_pack_not_text():
    line = cs.CallSettings.from_mapping(FLUX, env={}).describe()
    assert "flux-hannah-en" in line and "account_payments" in line
    assert FLUX["greeting_text"] not in line


# ---- TTS factory ------------------------------------------------------------------------


def test_aura_settings_build_the_aura_service():
    s = cs.CallSettings.from_mapping({"tts_model": "aura-2", "tts_voice": "aura-2-thalia-en"})
    tts = cs.build_tts(s, api_key="k")
    assert type(tts) is DeepgramTTSService
    assert tts._settings.voice == "aura-2-thalia-en"


def test_flux_settings_build_the_flux_service_on_v2():
    tts = cs.build_tts(cs.CallSettings.from_mapping(FLUX, env={}), api_key="k")
    assert isinstance(tts, DeepgramFluxTTSService)
    assert tts._settings.voice == "flux-hannah-en"
    assert tts._url == "wss://api.deepgram.com/v2/speak"


# ---- intake and hold use them -------------------------------------------------------


@pytest.fixture
async def session():
    s = intake.IntakeSession(
        pack=load_pack("claims_status"),
        store=MemoryCallStore(),
        session_id="s1",
        greeting=FLUX["greeting_text"],
        identity_read_back=False,
    )
    await s.start()
    return s


def test_greeting_node_speaks_the_configured_greeting(session):
    node = session.greeting_node()
    assert {"type": "tts_say", "text": FLUX["greeting_text"]} in node["pre_actions"]


async def test_read_back_off_checks_identity_right_after_save(session):
    node = session.identity_node()
    f = next(f for f in node["functions"] if f.name == "save_identity")
    result, nxt = await f.handler(
        {"full_name": "Maria Alvarez", "date_of_birth": "1984-03-12", "member_id": "EVM100231"},
        object(),
    )
    assert nxt["name"] == "reason"
    assert "identity" in result


async def test_read_back_on_is_still_the_default():
    s = intake.IntakeSession(
        pack=load_pack("claims_status"), store=MemoryCallStore(), session_id="s2"
    )
    await s.start()
    node = s.identity_node()
    f = next(f for f in node["functions"] if f.name == "save_identity")
    _, nxt = await f.handler(
        {"full_name": "Maria Alvarez", "date_of_birth": "1984-03-12", "member_id": "EVM100231"},
        object(),
    )
    assert nxt["name"] == "identity_confirm"


async def test_hold_controller_speaks_the_configured_hold_line():
    spoken = []

    async def queue(frames):
        spoken.extend(f.text for f in frames if isinstance(f, TTSSpeakFrame))

    s = cs.CallSettings.from_mapping(FLUX, env={})
    ctl = hold.HoldController(queue, interval=0.02, line=s.hold_line)
    await ctl.on_app_message({"type": hold.MESSAGE_TYPE, "event": "queued", "position": 2}, "api")
    import asyncio

    await asyncio.sleep(0.07)
    ctl.stop()
    assert spoken and spoken[0] == "Still with us. You're number 2 in line."
