"""Ask bar (P4-6, AP-8): the agent types a question; the answer is a supporting card.

Same connectors and pack as the rest of the assist: a question names a connector
("claims", "payments", ...) and the pack must allow it. Records come from the
connector and are rendered with the pack's card fields; nothing the LLM writes
reaches the screen.
"""

from __future__ import annotations

import pytest
from agent_assist_bot import assist_contract as c
from agent_assist_bot.assist_ask import AskQuery, parse_ask
from agent_assist_bot.packs import load_packs
from assist_fakes import FakeLLM
from test_assist import MARIA_INTAKE, Harness


@pytest.fixture(scope="module")
def packs():
    return load_packs()


# --- parsing ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("Show the last 3 claims", AskQuery("claims", {}, 3)),
        ("show me her claims", AskQuery("claims", {}, 5)),
        ("last two payments", AskQuery("payments", {}, 2)),
        ("any denied claims?", AskQuery("claims", {"status": "denied"}, 5)),
        ("What prior authorizations does she have", AskQuery("prior_authorizations", {}, 5)),
        ("referrals", AskQuery("referrals", {}, 5)),
        ("show the last 10 calls", AskQuery("interactions", {}, 5)),
        ("premium charges this year", AskQuery("payments", {"kind": "premium"}, 5)),
        ("what plan is she on", AskQuery("members", {}, 1)),
        ("claims that needed a prior authorization", AskQuery("claims", {}, 5)),
    ],
)
def test_parse_ask_names_the_connector_and_limit(text, want):
    assert parse_ask(text) == want


@pytest.mark.parametrize("text", ["", "   ", "what is an EOB", "how do I file an appeal"])
def test_parse_ask_without_a_connector_word_is_none(text):
    assert parse_ask(text) is None


# --- contract --------------------------------------------------------------------------


def _ask_message(d, t=c.ASK_TYPE):
    return {"label": "rtvi-ai", "type": "client-message", "id": "m1", "data": {"t": t, "d": d}}


def test_parse_ask_message_from_the_agent_only():
    msg = _ask_message({"v": 1, "ask_id": "ask-1", "text": " Show the last 3 claims "})
    assert c.parse_ask_message(msg, "agent-1", "agent-1") == c.Ask(
        "ask-1", "Show the last 3 claims"
    )
    assert c.parse_ask_message(msg, "caller-1", "agent-1") is None
    assert c.parse_ask_message(msg, "api", "agent-1") is None
    assert c.parse_ask_message(msg, "agent-1", None) is None


@pytest.mark.parametrize(
    "d",
    [
        {"ask_id": "a", "text": ""},
        {"ask_id": "", "text": "claims"},
        {"ask_id": "a", "text": "x" * 301},
        {"ask_id": "a"},
        {"v": 2, "ask_id": "a", "text": "claims"},
    ],
)
def test_parse_ask_message_rejects_bad_fields(d):
    assert c.parse_ask_message(_ask_message(d), "agent-1", "agent-1") is None


def test_ask_result_message():
    m = c.ask_result_message("ask-1", "no_answer", None, seq=3, ts=1.0, message="Nothing found.")
    assert m == {
        "type": "assist",
        "v": 1,
        "event": "ask_result",
        "seq": 3,
        "ts": 1.0,
        "ask_id": "ask-1",
        "status": "no_answer",
        "card_id": None,
        "message": "Nothing found.",
    }
    with pytest.raises(ValueError):
        c.ask_result_message("ask-1", "maybe", None, seq=1, ts=0)


# --- engine ----------------------------------------------------------------------------


async def maria(packs, **kw) -> Harness:
    h = Harness(packs, **kw)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    return h


def results(h) -> list[dict]:
    return h.events_of("ask_result")


async def test_show_the_last_3_claims_is_a_record_card_with_its_source(packs):
    h = await maria(packs)
    h.clock.advance(1.5)
    await h.engine.on_ask(c.Ask("ask-1", "Show the last 3 claims"))
    [card] = h.added()
    assert card["id"] == "ask:ask-1"
    assert card["kind"] == "record"
    assert card["title"] == "Claims"
    assert card["ai_suggested"] is True
    assert card["trigger"]["role"] == "ask"
    assert card["trigger"]["text"] == "Show the last 3 claims"
    assert card["trigger"]["phrase"] is None
    assert card["source"]["label"] == "Claims system"
    assert [r["id"] for r in card["source"]["records"]] == ["70412", "70358"]
    # the pack's claims table fields, in its order
    assert card["body"]["fields"][:2] == ["claim_id", "service_date"]
    assert len(card["body"]["rows"]) == 2
    assert ("claims_search", {"limit": 3}) in h.connectors.calls
    [res] = results(h)
    assert res["ask_id"] == "ask-1" and res["status"] == "answered"
    assert res["card_id"] == "ask:ask-1"
    # the card is a supporting card the agent can pin, dismiss, rate
    assert h.engine.supporting["ask:ask-1"].kind == "record"
    sent = [e for e in h.events if e[0] == "card_sent" and e[1] == "ask:ask-1"]
    assert sent and sent[0][2]["ask"] is True and "latency_ms" in sent[0][2]


async def test_limit_caps_the_rows(packs):
    h = await maria(packs)
    await h.engine.on_ask(c.Ask("a1", "show the last 1 claim"))
    [card] = h.added()
    assert [r["claim_id"] for r in card["body"]["rows"]] == ["70412"]


async def test_filters_go_to_the_connector(packs):
    h = await maria(packs)
    await h.engine.on_ask(c.Ask("a1", "any denied claims?"))
    [card] = h.added()
    assert ("claims_search", {"status": "denied", "limit": 5}) in h.connectors.calls
    assert [r["claim_id"] for r in card["body"]["rows"]] == ["70412"]


async def test_a_connector_the_pack_does_not_allow_is_not_called(packs):
    h = await maria(packs)
    await h.engine.on_ask(c.Ask("a1", "show the last 3 payments"))
    assert h.added() == []
    assert not any(name.startswith("payments_") for name, _ in h.connectors.calls)
    [res] = results(h)
    assert res["status"] == "no_answer" and res["card_id"] is None
    assert "Claim status" in res["message"]


async def test_no_rows_is_no_answer(packs):
    h = await maria(packs)
    await h.engine.on_ask(c.Ask("a1", "show her referrals"))
    assert h.added() == []
    [res] = results(h)
    assert res["status"] == "no_answer"


async def test_asks_are_not_rate_limited(packs):
    h = await maria(packs, max_cards_per_min=1)
    await h.say("caller", "So why did my MRI claim get denied?")
    assert h.engine._pending  # the limit is full
    await h.engine.on_ask(c.Ask("a1", "Show the last 3 claims"))
    assert "ask:a1" in [c_["id"] for c_ in h.added()]


async def test_each_ask_is_its_own_card(packs):
    h = await maria(packs)
    await h.engine.on_ask(c.Ask("a1", "Show the last 3 claims"))
    await h.engine.on_ask(c.Ask("a2", "Show the last 1 claim"))
    assert [c_["id"] for c_ in h.added()] == ["ask:a1", "ask:a2"]


async def test_kb_question_without_a_connector_word_searches_articles(packs):
    h = await maria(packs)
    await h.engine.on_ask(c.Ask("a1", "what is an explanation of benefits"))
    [card] = h.added()
    assert card["kind"] == "article"
    assert card["source"]["label"] == "Knowledge base"
    assert card["source"]["records"] == [{"connector": "kb_articles", "id": "KB-CL-010"}]


async def test_llm_picks_records_for_other_questions_and_the_engine_renders_them(packs):
    llm = FakeLLM({"next_steps": [], "cards": [{"template_id": "denied_claim", "ids": ["70412"]}]})
    h = await maria(packs, llm=llm)
    await h.engine.on_ask(c.Ask("a1", "why was the MRI turned down"))
    [card] = h.added()
    assert card["id"] == "ask:a1" and card["kind"] == "record"
    assert card["body"]["rows"][0]["claim_id"] == "70412"
    assert card["trigger"]["role"] == "ask"
    [req] = llm.requests
    assert req.question == "why was the MRI turned down"


async def test_llm_proposing_nothing_is_no_answer(packs):
    h = await maria(packs, llm=FakeLLM({"next_steps": [], "cards": []}))
    await h.engine.on_ask(c.Ask("a1", "zzz qqq"))
    assert h.added() == []
    assert results(h)[0]["status"] == "no_answer"


async def test_llm_failure_is_an_error_result(packs):
    from assist_fakes import FailingLLM

    h = await maria(packs, llm=FailingLLM())
    await h.engine.on_ask(c.Ask("a1", "zzz qqq"))
    assert results(h)[0]["status"] == "error"


# --- general question pack (P4-6 exit) -------------------------------------------------


async def test_intake_matching_no_pack_starts_on_the_fallback_pack(packs):
    h = Harness(packs)
    await h.engine.start(
        {"reason": "I got a paper called an explanation of benefits and I don't get it"},
        default_pack="claims_status",
    )
    assert h.engine.pack.id == "general_question"


async def test_empty_intake_keeps_the_default_pack(packs):
    h = Harness(packs)
    await h.engine.start({}, default_pack="claims_status")
    assert h.engine.pack.id == "claims_status"


async def test_fallback_pack_caller_question_gets_the_kb_article_without_the_llm(packs):
    h = Harness(packs)
    await h.engine.start({"reason": "a question about a paper"}, "general_question")
    line = await h.say("caller", "So what is an explanation of benefits, exactly?")
    [card] = h.added()
    assert card["id"] == "kb:KB-CL-010" and card["kind"] == "article"
    assert card["trigger"]["line_id"] == line["line_id"]
    assert card["trigger"]["phrase"] == "explanation of benefits"
    span = line["text"][card["trigger"]["start"] : card["trigger"]["end"]]
    assert span.lower() == "explanation of benefits"


async def test_fallback_pack_small_talk_shows_nothing(packs):
    h = Harness(packs)
    await h.engine.start({"reason": "a question"}, "general_question")
    await h.say("agent", "Sure, let me take a look.")
    await h.say("caller", "Okay, thanks.")
    assert h.added() == []
