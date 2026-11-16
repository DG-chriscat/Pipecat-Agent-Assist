"""P5-9 (DC-11): the LLM per job for live tests, set the way the Settings page saves it.

LLM_INTAKE, LLM_ASSIST, LLM_WRAPUP (claude-haiku-5-5, phonellm-mini-alpha-2-fp8,
think-fast) override the defaults for the calls a live test runs; unset keeps them.
"""

from __future__ import annotations

import os

ENV = {"llm_intake": "LLM_INTAKE", "llm_assist": "LLM_ASSIST", "llm_wrapup": "LLM_WRAPUP"}


def llm_overrides(env=None) -> dict[str, str]:
    env = os.environ if env is None else env
    return {key: env[name] for key, name in ENV.items() if env.get(name)}


def llm_settings_store(env=None):
    """A server settings store with the env's LLM choices on top of the defaults."""
    from agent_assist_server import bot_settings

    over = llm_overrides(env)
    return bot_settings.MemorySettingsStore(defaults=lambda: {**bot_settings.defaults(), **over})
