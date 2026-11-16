"""Bot settings (P5-5, PRD AP-20, IN-11): defaults, validation, the memory store, and the
change log. No network, no database (the Postgres store is in test_bot_settings_db.py)."""

from __future__ import annotations

import pytest
from agent_assist_server import bot_settings as bs

CATALOG = bs.StaticVoiceCatalog(
    {
        "aura-2": [bs.Voice("aura-2-thalia-en", "Thalia"), bs.Voice("aura-2-orion-en", "Orion")],
        "flux": [bs.Voice("flux-hannah-en", "Hannah"), bs.Voice("flux-alexis-en", "Alexis")],
    }
)
PACKS = {"claims_status", "account_payments", "general_question"}


def good(**over) -> dict:
    return {**bs.defaults({}), **over}


async def check(values: dict) -> dict[str, str]:
    return await bs.validate(values, catalog=CATALOG, packs=PACKS)


def test_defaults_cover_every_field():
    d = bs.defaults({})
    assert set(d) == set(bs.FIELDS)
    assert d["tts_model"] in bs.TTS_MODELS
    assert d["intake_pack"] == "claims_status"
    assert d["identity_read_back"] is True
    assert d["hold_update_secs"] == 60


def test_default_greeting_keeps_the_ai_disclosure():
    assert bs.disclosure_problems(bs.defaults({})["greeting_text"]) == []


def test_default_hold_line_is_todays_line():
    d = bs.defaults({})
    assert d["hold_update_text"] == (
        "Thanks for waiting. {place} A person will be with you shortly."
    )


def test_env_overrides_the_default_pack_and_hold_interval():
    d = bs.defaults({"INTAKE_PACK": "account_payments", "HOLD_UPDATE_SECS": "8"})
    assert d["intake_pack"] == "account_payments"
    assert d["hold_update_secs"] == 8


@pytest.mark.parametrize("raw", ["", "abc", "-5", "0"])
def test_bad_env_hold_interval_keeps_the_default(raw):
    assert bs.defaults({"HOLD_UPDATE_SECS": raw})["hold_update_secs"] == 60


async def test_defaults_are_valid():
    assert await check(good()) == {}


async def test_flux_voice_with_flux_model_is_valid():
    assert await check(good(tts_model="flux", tts_voice="flux-hannah-en")) == {}


async def test_voice_must_be_in_the_chosen_models_catalog():
    errors = await check(good(tts_model="flux", tts_voice="aura-2-thalia-en"))
    assert "tts_voice" in errors


async def test_unknown_model_is_rejected():
    errors = await check(good(tts_model="aura-1"))
    assert "tts_model" in errors


@pytest.mark.parametrize(
    "greeting",
    [
        "Hi, thanks for calling. I'll ask a few quick questions.",
        "Hi, you're speaking with an AI assistant. I'll ask a few questions.",
        "Hi, I'll ask a few questions, and then a person will join the call.",
    ],
)
async def test_greeting_must_keep_the_ai_disclosure(greeting):
    errors = await check(good(greeting_text=greeting))
    assert "greeting_text" in errors


def test_disclosure_problems_name_what_is_missing():
    assert bs.disclosure_problems("Hello there.") == [bs.NEEDS_AI, bs.NEEDS_PERSON]
    assert bs.disclosure_problems("Hi, I'm an AI assistant.") == [bs.NEEDS_PERSON]
    assert bs.disclosure_problems("A person will join you soon.") == [bs.NEEDS_AI]


@pytest.mark.parametrize(
    "greeting",
    [
        "Hello, this is Vitals, an AI assistant. A person will join after a few questions.",
        "Hi. I'm an automated AI helper; a human will join the call shortly after.",
        "Thanks for calling. You're talking to an artificial intelligence assistant, "
        "and an agent will join you after intake.",
    ],
)
def test_reworded_greetings_still_pass(greeting):
    assert bs.disclosure_problems(greeting) == []


def test_ai_is_a_whole_word():
    assert bs.NEEDS_AI in bs.disclosure_problems("I said a person will join.")


async def test_lengths_are_bounded():
    long = "You're speaking with an AI assistant and a person will join. " * 20
    errors = await check(good(greeting_text=long, hold_update_text="x" * 400))
    assert set(errors) == {"greeting_text", "hold_update_text"}


async def test_blank_hold_message_is_rejected():
    assert "hold_update_text" in await check(good(hold_update_text="   "))


async def test_hold_message_allows_only_the_place_placeholder():
    assert await check(good(hold_update_text="Still here. {place}")) == {}
    assert "hold_update_text" in await check(good(hold_update_text="Hi {name}."))
    assert "hold_update_text" in await check(good(hold_update_text="Hi {place"))


@pytest.mark.parametrize("secs", [4, 601, 30.5, "60", True])
async def test_hold_interval_is_bounded_whole_seconds(secs):
    assert "hold_update_secs" in await check(good(hold_update_secs=secs))


async def test_pack_must_exist():
    assert "intake_pack" in await check(good(intake_pack="nope"))


async def test_read_back_must_be_a_bool():
    assert "identity_read_back" in await check(good(identity_read_back="yes"))


async def test_missing_and_unknown_fields_are_errors():
    values = good()
    del values["tts_voice"]
    values["surprise"] = 1
    errors = await check(values)
    assert set(errors) == {"tts_voice", "surprise"}


def test_hold_line_fills_the_place():
    t = bs.defaults({})["hold_update_text"]
    assert bs.render_hold_line(t, 1) == (
        "Thanks for waiting. You're next in line. A person will be with you shortly."
    )
    assert "number 3 in line" in bs.render_hold_line(t, 3)
    assert "You're still in line." in bs.render_hold_line(t, None)


def test_changes_lists_only_changed_fields():
    before = good()
    after = good(tts_model="aura-2", tts_voice="aura-2-orion-en")
    assert bs.changes(before, after) == {
        "tts_model": {"from": before["tts_model"], "to": "aura-2"},
        "tts_voice": {"from": before["tts_voice"], "to": "aura-2-orion-en"},
    }


async def test_memory_store_starts_at_defaults():
    store = bs.MemorySettingsStore(defaults=lambda: good())
    current = await store.get()
    assert current.values == good() and current.updated_at is None and current.is_default


async def test_memory_store_saves_and_logs_who_changed_what():
    store = bs.MemorySettingsStore(defaults=lambda: good())
    saved = await store.save(good(tts_voice="aura-2-orion-en"), session_id="sess-1", agent="Ana")
    assert saved.values["tts_voice"] == "aura-2-orion-en" and not saved.is_default
    assert saved.updated_by == "sess-1" and saved.updated_at is not None
    (event,) = store.events
    assert event.kind == "save" and event.session_id == "sess-1" and event.agent == "Ana"
    assert event.changes == {"tts_voice": {"from": good()["tts_voice"], "to": "aura-2-orion-en"}}
    assert (await store.get()).values == saved.values


async def test_memory_store_reset_goes_back_to_defaults_and_logs():
    store = bs.MemorySettingsStore(defaults=lambda: good())
    await store.save(good(hold_update_secs=30), session_id="s1", agent=None)
    reset = await store.reset(session_id="s2", agent=None)
    assert reset.values == good() and reset.is_default
    assert [e.kind for e in store.events] == ["save", "reset"]
    assert store.events[1].changes == {"hold_update_secs": {"from": 30, "to": 60}}


async def test_defaults_are_read_when_asked_not_frozen_at_start():
    env = {"HOLD_UPDATE_SECS": "8"}
    store = bs.MemorySettingsStore(defaults=lambda: bs.defaults(env))
    assert (await store.get()).values["hold_update_secs"] == 8
    env["HOLD_UPDATE_SECS"] = "9"
    assert (await store.get()).values["hold_update_secs"] == 9


def test_stt_defaults_are_nova_3_on_both_sides_after_the_flux_identity_test():
    # docs/spikes/stt-flux-intake.md: Flux intake missed Linda Nguyen's name 5 of 5
    # (identity 8 of 10 vs Nova-3 10 of 10), so Nova-3 stays the intake default.
    d = bs.defaults({})
    assert d["stt_intake_model"] == "nova-3-general"
    assert d["stt_assist_model"] == "nova-3-general"


@pytest.mark.parametrize("model", ["flux-general-en", "nova-3-general"])
async def test_intake_stt_model_choices_are_valid(model):
    assert await check(good(stt_intake_model=model)) == {}


@pytest.mark.parametrize("model", ["nova-3-general", "nova-3-medical", "flux-general-en"])
async def test_assist_stt_model_choices_are_valid(model):
    assert await check(good(stt_assist_model=model)) == {}


@pytest.mark.parametrize("model", ["nova-3-medical", "nova-2", "", None, 3])
async def test_intake_stt_model_outside_its_list_is_rejected(model):
    # No Flux medical model, and intake turn taking needs Flux or Nova-3 general.
    errors = await check(good(stt_intake_model=model))
    assert set(errors) == {"stt_intake_model"}


@pytest.mark.parametrize("model", ["flux-general-multi", "nova-2", "", None])
async def test_assist_stt_model_outside_its_list_is_rejected(model):
    errors = await check(good(stt_assist_model=model))
    assert set(errors) == {"stt_assist_model"}


async def test_a_saved_row_missing_a_newer_field_gets_its_default():
    store = bs.MemorySettingsStore(defaults=lambda: good())
    partial = good()
    del partial["hold_update_text"]
    store._row = partial  # an old row from before a field existed
    assert (await store.get()).values == good()


# ---- Flux voice controls (P5-8, AP-22) ------------------------------------------------


def test_flux_control_defaults():
    d = bs.defaults({})
    assert d["tts_speed"] == 1.0
    assert d["tts_expressivity"] == 0
    assert d["slow_readback"] is True
    assert d["readback_speed"] == 0.9


@pytest.mark.parametrize("speed", [bs.MIN_TTS_SPEED, 0.95, 1.0, 1.05, bs.MAX_TTS_SPEED, 1])
async def test_speed_on_a_step_in_range_is_valid(speed):
    assert await check(good(tts_speed=speed)) == {}


@pytest.mark.parametrize("speed", [0.5, 1.5, 1.03, "1.0", None, True])
async def test_speed_off_the_range_or_step_is_rejected(speed):
    assert set(await check(good(tts_speed=speed))) == {"tts_speed"}


def test_speed_range_is_inside_what_flux_takes():
    assert 0.5 <= bs.MIN_TTS_SPEED < 1.0 < bs.MAX_TTS_SPEED <= 1.5


@pytest.mark.parametrize("value", [-2, -1, 0, 1, 2])
async def test_expressivity_steps_are_valid(value):
    assert await check(good(tts_expressivity=value)) == {}


@pytest.mark.parametrize("value", [-3, 3, 0.5, "0", None, False])
async def test_expressivity_outside_minus_2_to_2_is_rejected(value):
    assert set(await check(good(tts_expressivity=value))) == {"tts_expressivity"}


@pytest.mark.parametrize("speed", [0.8, 0.85, 0.9, 0.95, 1.0])
async def test_readback_speed_from_0_8_to_1_0_is_valid(speed):
    assert await check(good(readback_speed=speed)) == {}


@pytest.mark.parametrize("speed", [0.75, 1.05, 0.93, None, "0.9"])
async def test_readback_speed_outside_0_8_to_1_0_is_rejected(speed):
    assert set(await check(good(readback_speed=speed))) == {"readback_speed"}


async def test_slow_readback_must_be_a_bool():
    assert set(await check(good(slow_readback="yes"))) == {"slow_readback"}


async def test_flux_controls_are_still_checked_but_ignored_with_aura_2():
    # Saved with Aura-2 they are kept for when Flux is picked again; the bot ignores them.
    aura = good(tts_model="aura-2", tts_voice="aura-2-thalia-en", tts_speed=1.1)
    assert await check(aura) == {}
    assert set(await check({**aura, "tts_speed": 2.0})) == {"tts_speed"}


# ---- LLM per job (P5-9, DC-11) --------------------------------------------------------

LLM_FIELDS = ("llm_intake", "llm_assist", "llm_wrapup")


def test_llm_models_are_claude_phonellm_and_think_fast():
    assert bs.LLM_MODELS == ("claude-haiku-5-5", "phonellm-mini-alpha-2-fp8", "think-fast")


def test_llm_defaults_come_from_the_measure():
    # docs/spikes/llm-deepgram.md decides each job's default.
    d = bs.defaults({})
    assert {k: d[k] for k in LLM_FIELDS} == bs.DEFAULT_LLMS
    assert set(bs.DEFAULT_LLMS.values()) <= set(bs.LLM_MODELS)


@pytest.mark.parametrize("field", LLM_FIELDS)
@pytest.mark.parametrize("model", ["claude-haiku-5-5", "phonellm-mini-alpha-2-fp8", "think-fast"])
async def test_each_job_takes_each_llm(field, model):
    assert await check(good(**{field: model})) == {}


@pytest.mark.parametrize("field", LLM_FIELDS)
@pytest.mark.parametrize("model", ["gpt-4o", "", None, 3, "claude-sonnet-5"])
async def test_an_llm_outside_the_list_is_rejected(field, model):
    errors = await check(good(**{field: model}))
    assert set(errors) == {field}


def test_llm_catalog_marks_deepgram_models_early_access_and_think_fast_training():
    by_id = {m["id"]: m for m in bs.llm_catalog()}
    assert list(by_id) == list(bs.LLM_MODELS)
    assert by_id["claude-haiku-5-5"]["early_access"] is False
    assert by_id["claude-haiku-5-5"]["note"] is None
    assert by_id["phonellm-mini-alpha-2-fp8"]["early_access"] is True
    assert by_id["phonellm-mini-alpha-2-fp8"]["note"] is None
    assert by_id["think-fast"]["early_access"] is True
    assert by_id["think-fast"]["note"] == "Always stored for Deepgram model training (no opt out)"
    assert {m["provider"] for m in by_id.values()} == {"anthropic", "deepgram"}
