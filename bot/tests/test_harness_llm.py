from harness.llm import llm_overrides, llm_settings_store


def test_llm_overrides_take_only_the_set_vars():
    env = {"LLM_ASSIST": "think-fast", "LLM_WRAPUP": "", "OTHER": "x"}
    assert llm_overrides(env) == {"llm_assist": "think-fast"}


async def test_settings_store_puts_the_overrides_on_the_defaults():
    from agent_assist_server import bot_settings

    store = llm_settings_store({"LLM_INTAKE": "phonellm-mini-alpha-2-fp8"})
    values = (await store.get()).values
    assert values["llm_intake"] == "phonellm-mini-alpha-2-fp8"
    assert values["llm_assist"] == bot_settings.DEFAULT_LLMS["llm_assist"]
