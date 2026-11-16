"""Assist engine (P4-3): ranking, hero replacement, rate limit, plan ticks, guardrail.

Real packs from packs/, fake connectors over in-memory rows, a scripted fake LLM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from agent_assist_bot import assist
from agent_assist_bot.assist import AssistEngine, clinical_advice
from agent_assist_bot.assist_contract import Feedback
from agent_assist_bot.packs import PACKS_DIR, Pack, load_packs
from assist_fakes import Clock, FailingLLM, FakeConnectors, FakeLLM, default_rows

MARIA_INTAKE = {
    "full_name": "Maria Alvarez",
    "member_id": "EVM100231",
    "reason": "Her MRI claim was denied",
    "claim_number": "CLM-900001",
    "claim_concern": "denied, the letter mentioned no prior authorization",
    "summary": "Maria Alvarez is calling about MRI claim CLM-900001, which was denied.",
}

PENDING_CLAIM = {
    "claim_id": "CLM-900003",
    "service_date": "2026-10-20",
    "provider": "Northgate Cardiology",
    "service": "Echocardiogram",
    "billed_amount": 900.0,
    "status": "pending_info",
    "denial_code": None,
    "denial_reason": None,
    "received_on": "2026-10-22",
    "processed_on": None,
}
NETWORK_CLAIM = {
    "claim_id": "CLM-900005",
    "service_date": "2026-08-02",
    "provider": "Bayview Dermatology",
    "service": "Skin biopsy",
    "billed_amount": 400.0,
    "status": "denied",
    "denial_code": "CO-242",
    "denial_reason": "Services not provided by network provider",
    "received_on": "2026-08-05",
    "processed_on": "2026-08-20",
}


@pytest.fixture(scope="module")
def packs():
    return load_packs()


class Harness:
    def __init__(self, packs, *, llm=None, rows=None, max_cards_per_min=4, connectors=None):
        self.sent: list[dict] = []
        self.events: list[tuple[str, str | None, dict]] = []
        self.clock = Clock()
        self.connectors = connectors or FakeConnectors(rows)
        self.engine = AssistEngine(
            packs=packs,
            connectors=self.connectors,
            llm=llm,
            send=self._send,
            record=lambda kind, card_id, data: self.events.append((kind, card_id, data)),
            clock=self.clock,
            max_cards_per_min=max_cards_per_min,
            llm_debounce_secs=None,
        )
        self._n = {"caller": 0, "agent": 0}

    async def _send(self, data: dict) -> None:
        self.sent.append(data)

    async def say(self, role: str, text: str) -> dict:
        self._n[role] += 1
        line = {
            "type": "transcript",
            "role": role,
            "text": text,
            "is_final": True,
            "ts": self.clock(),
            "line_id": f"{role}-{self._n[role]}",
        }
        await self.engine.on_line(line)
        return line

    def events_of(self, event: str) -> list[dict]:
        return [m for m in self.sent if m["event"] == event]

    def heroes(self) -> list[dict]:
        return self.events_of("hero")

    def added(self) -> list[dict]:
        return [m["card"] for m in self.events_of("card_added")]


async def maria(packs, **kw) -> Harness:
    h = Harness(packs, **kw)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    return h


# --- start ---------------------------------------------------------------------------


async def test_start_sends_status_and_the_pack_plan(packs):
    h = await maria(packs)
    status, plan = h.sent[0], h.sent[1]
    assert status["event"] == "assist_status" and status["state"] == "listening"
    assert status["pack_id"] == "claims_status"
    assert plan["event"] == "plan_update" and plan["pack_id"] == "claims_status"
    assert [s["id"] for s in plan["steps"]] == [
        "confirm_claim",
        "explain_status",
        "take_next_step",
        "set_expectations",
    ]
    assert plan["done"] == 0 and all(m["v"] == 1 and m["type"] == "assist" for m in h.sent)
    assert [m["seq"] for m in h.sent] == [1, 2]


# --- deterministic trigger path --------------------------------------------------------


async def test_trigger_words_make_the_retro_auth_hero_without_the_llm(packs):
    h = await maria(packs)
    line = await h.say(
        "caller", "The letter said it was denied, something about prior authorization."
    )
    [hero] = h.heroes()
    card = hero["card"]
    assert hero["reason"] == "new" and hero["replaces"] is None
    assert card["kind"] == "next_step" and card["rule_id"] == "retro_auth_review"
    assert card["id"] == "nbs:retro_auth_review:CLM-900001"
    assert card["body"]["headline"] == "Start a retro authorization review for CLM-900001"
    assert "MRI, knee, left" in card["body"]["script"]
    assert card["body"]["action"] == {
        "id": "request_retro_auth_review",
        "label": "Request retro auth review",
    }
    # Every card carries its trigger text and source.
    t = card["trigger"]
    assert t["text"] == line["text"] and t["line_id"] == line["line_id"]
    assert t["role"] == "caller" and t["phrase"] == "denied"
    assert line["text"][t["start"] : t["end"]].lower() == "denied"
    assert card["source"]["label"] == "Claims system, KB-CL-002 and KB-CL-009"
    assert card["source"]["records"] == [{"connector": "claims", "id": "CLM-900001"}]
    assert card["ai_suggested"] is True


async def test_hero_comes_with_its_record_card(packs):
    h = await maria(packs)
    await h.say("caller", "My MRI claim was denied.")
    [rec] = h.added()
    assert rec["kind"] == "record" and rec["template_id"] == "denied_claim"
    assert [r["claim_id"] for r in rec["body"]["rows"]] == ["CLM-900001"]
    assert rec["body"]["summary"].startswith("CLM-900001 MRI, knee, left denied CO-197")
    assert rec["source"]["label"] == "Claims system"
    assert rec["trigger"]["phrase"] in ("denied", "my claim")
    # Hero is sent before its supporting card.
    kinds = [m["event"] for m in h.sent[2:]]
    assert kinds.index("hero") < kinds.index("card_added")


async def test_hero_is_sent_before_supporting_lookups_run(packs):
    # DC-6: the hero goes out after one connector round trip, not after every card.
    class Spy(FakeConnectors):
        def __init__(self, harness_sent):
            super().__init__()
            self.sent_at_call: list[tuple[str, int]] = []
            self._sent = harness_sent

        async def call(self, name, args=None):
            self.sent_at_call.append((name, len(self._sent)))
            return await super().call(name, args)

    h = Harness(packs)
    spy = Spy(h.sent)
    h.connectors = h.engine.connectors = spy
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    before = len(h.sent)
    await h.say("caller", "It was denied.")
    first_after_hero = [n for name, n in spy.sent_at_call if n > before]
    assert spy.sent_at_call[0] == ("claims_search", before)  # hero lookup
    assert first_after_hero, "record card lookups ran before the hero was sent"
    assert h.sent[before]["event"] == "hero"


async def test_line_without_triggers_makes_no_cards(packs):
    h = await maria(packs)
    await h.say("caller", "Hi, thanks for taking my call.")
    assert h.heroes() == [] and h.added() == []
    assert h.connectors.calls == []


async def james(packs) -> Harness:
    h = Harness(packs)
    await h.engine.start(
        {"full_name": "James Chen", "reason": "I was charged twice for this month's premium."},
        default_pack="claims_status",
    )
    assert h.engine.pack.id == "account_payments"
    return h


async def test_trigger_phrase_split_across_two_final_lines_still_fires(packs):
    # Live STT (Phase 4 exit run 7) ended a final line inside the phrase:
    # "and I was charged" / "twice for my premium."
    h = await james(packs)
    await h.say("caller", "and I was charged")
    h.clock.advance(1.3)
    line = await h.say("caller", "twice for my premium.")
    [hero] = h.heroes()
    card = hero["card"]
    assert card["id"] == "nbs:duplicate_premium_refund:PAY-000064"
    t = card["trigger"]
    # The trigger is the line that completed the phrase; its own part is underlined.
    assert t["line_id"] == line["line_id"] and t["phrase"] == "charged twice"
    assert line["text"][t["start"] : t["end"]] == "twice"


async def test_split_phrase_needs_the_same_speaker_and_a_short_gap(packs):
    h = await james(packs)
    await h.say("agent", "So you were charged")
    await h.say("caller", "twice for my premium.")
    assert h.heroes() == []
    h2 = await james(packs)
    await h2.say("caller", "and I was charged")
    h2.clock.advance(10)
    await h2.say("caller", "twice for my premium.")
    assert h2.heroes() == []


async def test_a_phrase_already_in_the_previous_line_does_not_fire_again(packs):
    h = await james(packs)
    await h.say("caller", "I was charged twice.")
    assert len(h.heroes()) == 1
    hero_id = h.heroes()[0]["card"]["id"]
    await h.engine.on_feedback(Feedback(card_id=hero_id, action="dismiss"))
    n = len(h.sent)
    await h.say("caller", "Okay.")
    assert [m for m in h.sent[n:] if m["event"] in ("hero", "card_added")] == []


async def test_same_trigger_again_does_not_repeat_cards(packs):
    h = await maria(packs)
    await h.say("caller", "It was denied.")
    n = len(h.sent)
    await h.say("caller", "Yes, denied, like I said.")
    assert len(h.sent) == n


# --- ranking and hero replacement -----------------------------------------------------


def _three_claims():
    rows = default_rows()
    rows["claims"] = rows["claims"] + [dict(PENDING_CLAIM), dict(NETWORK_CLAIM)]
    return rows


async def test_higher_rank_replaces_hero_and_old_hero_becomes_supporting(packs):
    h = await maria(packs, rows=_three_claims())
    await h.say("caller", "My heart test claim is still pending.")
    first = h.heroes()[-1]["card"]
    assert first["rule_id"] == "provider_info_follow_up"

    await h.say("caller", "And the MRI one was denied for prior authorization.")
    hero = h.heroes()[-1]
    assert hero["card"]["rule_id"] == "retro_auth_review"
    assert hero["reason"] == "higher_rank" and hero["replaces"] == first["id"]
    demoted = [c for c in h.added() if c["id"] == first["id"]]
    assert demoted and demoted[0]["kind"] == "next_step"


async def test_lower_rank_never_replaces_the_hero(packs):
    h = await maria(packs, rows=_three_claims())
    await h.say("caller", "The MRI claim was denied, no prior authorization.")
    hero_id = h.heroes()[-1]["card"]["id"]
    heroes_before = len(h.heroes())
    await h.say("caller", "Another one was out of network.")
    assert len(h.heroes()) == heroes_before
    assert h.engine.hero.id == hero_id
    supporting = [c for c in h.added() if c["kind"] == "next_step"]
    assert [c["rule_id"] for c in supporting] == ["out_of_network_appeal"]


async def test_mentioned_record_ranks_first_among_rows(packs):
    rows = default_rows()
    other = dict(rows["claims"][0], claim_id="CLM-900099", service="CT scan")
    rows["claims"] = [other] + rows["claims"]  # newer CO-197 denial listed first
    h = await maria(packs, rows=rows)  # intake names CLM-900001
    await h.say("caller", "It was denied.")
    assert h.heroes()[-1]["card"]["id"] == "nbs:retro_auth_review:CLM-900001"


# --- feedback: accept, dismiss, pin, rate -------------------------------------------------


async def test_accept_logs_the_action_ticks_the_plan_and_promotes_the_next(packs):
    h = await maria(packs, rows=_three_claims())
    await h.say("caller", "The MRI claim was denied, no prior authorization.")
    await h.say("caller", "Another one was out of network.")
    hero = h.engine.hero
    await h.engine.on_feedback(Feedback(card_id=hero.id, action="accept"))

    kind, card_id, data = h.events[-1]
    assert (kind, card_id) == ("accept", hero.id)
    assert data["action_id"] == "request_retro_auth_review" and data["simulated"] is True
    plan = h.events_of("plan_update")[-1]
    step = next(s for s in plan["steps"] if s["id"] == "take_next_step")
    assert step["done"] and step["by"] == "action"

    nxt = h.heroes()[-1]
    assert nxt["reason"] == "accepted" and nxt["replaces"] == hero.id
    assert nxt["card"]["rule_id"] == "out_of_network_appeal"
    removed = h.events_of("card_removed")[-1]
    assert removed["card_id"] == nxt["card"]["id"] and removed["reason"] == "promoted"


async def test_accepted_card_is_not_suggested_again(packs):
    h = await maria(packs)
    await h.say("caller", "It was denied.")
    await h.engine.on_feedback(Feedback(card_id=h.engine.hero.id, action="accept"))
    n = len(h.heroes())
    await h.say("caller", "So the denial, prior authorization, right?")
    assert len(h.heroes()) == n and h.engine.hero is None


async def test_dismiss_clears_the_hero_back_to_listening(packs):
    h = await maria(packs)
    await h.say("caller", "It was denied.")
    hero_id = h.engine.hero.id
    await h.engine.on_feedback(Feedback(card_id=hero_id, action="dismiss"))
    last = h.heroes()[-1]
    assert last["card"] is None and last["reason"] == "dismissed" and last["replaces"] == hero_id
    assert h.events[-1][:2] == ("dismiss", hero_id)


async def test_dismiss_supporting_card_removes_it(packs):
    h = await maria(packs)
    await h.say("caller", "It was denied.")
    rec = h.added()[0]
    await h.engine.on_feedback(Feedback(card_id=rec["id"], action="dismiss"))
    removed = h.events_of("card_removed")[-1]
    assert removed == {**removed, "card_id": rec["id"], "reason": "dismissed"}


async def test_pin_and_rate_are_logged(packs):
    h = await maria(packs)
    await h.say("caller", "It was denied.")
    rec = h.added()[0]
    await h.engine.on_feedback(Feedback(card_id=rec["id"], action="pin"))
    await h.engine.on_feedback(Feedback(card_id=rec["id"], action="rate", rating="up"))
    assert [(k, c) for k, c, _ in h.events[-2:]] == [("pin", rec["id"]), ("rate", rec["id"])]
    assert h.events[-1][2]["rating"] == "up"


async def test_feedback_for_an_unknown_card_is_ignored(packs):
    h = await maria(packs)
    n = len(h.sent)
    await h.engine.on_feedback(Feedback(card_id="nbs:nope:1", action="accept"))
    assert len(h.sent) == n and not [e for e in h.events if e[0] == "accept"]


# --- rate limit (DC-7) --------------------------------------------------------------------


async def test_rate_limit_holds_cards_until_the_minute_frees_up(packs):
    h = await maria(packs, rows=_three_claims(), max_cards_per_min=2)
    await h.say("caller", "The MRI claim was denied, no prior authorization.")
    assert len(h.heroes()) + len(h.added()) == 2  # hero + record card
    await h.say("caller", "Another one was out of network.")
    assert len(h.heroes()) + len(h.added()) == 2
    status = h.events_of("assist_status")[-1]
    assert status["state"] == "rate_limited" and status["pending"] == 1

    h.clock.advance(61)
    await h.engine.flush()
    assert [c["rule_id"] for c in h.added() if c["kind"] == "next_step"] == [
        "out_of_network_appeal"
    ]
    assert h.events_of("assist_status")[-1]["state"] == "listening"


def test_rate_limit_default_and_env(monkeypatch):
    monkeypatch.delenv(assist.RATE_ENV, raising=False)
    assert assist.max_cards_from_env() == 4
    monkeypatch.setenv(assist.RATE_ENV, "6")
    assert assist.max_cards_from_env() == 6
    monkeypatch.setenv(assist.RATE_ENV, "zero")
    assert assist.max_cards_from_env() == 4


# --- latency (DC-6) -----------------------------------------------------------------------


async def test_latency_from_trigger_line_to_card_sent_is_logged_per_card(packs):
    h = await maria(packs)
    line = {
        "type": "transcript",
        "role": "caller",
        "text": "It was denied.",
        "is_final": True,
        "ts": h.clock() - 0.25,
        "line_id": "caller-1",
    }
    await h.engine.on_line(line)
    sent = [e for e in h.events if e[0] == "card_sent"]
    assert len(sent) == 2
    for _, _card_id, data in sent:
        assert data["latency_ms"] == pytest.approx(250, abs=1)
        assert data["trigger_phrase"] in ("denied", "my claim")
    assert h.engine.latencies_ms() == [pytest.approx(250, abs=1)] * 2


# --- call plan ticks (AP-12) ---------------------------------------------------------------


async def test_agent_phrases_tick_the_call_plan(packs):
    h = await maria(packs)
    await h.say("agent", "Okay Maria, I can see the claim right here.")
    plan = h.events_of("plan_update")[-1]
    step = next(s for s in plan["steps"] if s["id"] == "confirm_claim")
    assert step["done"] and step["by"] == "transcript" and plan["done"] == 1


async def test_caller_phrases_do_not_tick_the_plan(packs):
    h = await maria(packs)
    await h.say("caller", "I can see the letter, it says business days.")
    assert len(h.events_of("plan_update")) == 1


async def test_ticks_stay_done_and_are_sent_once(packs):
    h = await maria(packs)
    await h.say("agent", "I can see the claim.")
    await h.say("agent", "I can see it again.")
    assert len(h.events_of("plan_update")) == 2


# --- pack switching --------------------------------------------------------------------------


async def test_another_packs_trigger_switches_pack_with_alert_and_hero(packs):
    h = await maria(packs)
    await h.say("caller", "Also I was charged twice for my premium this month.")
    plan = h.events_of("plan_update")[-1]
    assert plan["pack_id"] == "account_payments"
    hero = h.heroes()[-1]["card"]
    assert hero["rule_id"] == "duplicate_premium_refund"
    assert hero["id"] == "nbs:duplicate_premium_refund:PAY-000064"
    alert = next(c for c in h.added() if c["kind"] == "alert")
    assert alert["template_id"] == "duplicate_charge"
    assert [r["payment_id"] for r in alert["body"]["rows"]] == ["PAY-000064"]


# --- guardrail ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "You should take ibuprofen for the pain.",
        "I recommend you stop taking the medication.",
        "That dosage is too high.",
        "It sounds like a diagnosis of arthritis.",
        "Your symptoms mean you need surgery.",
    ],
)
def test_clinical_advice_is_detected(text):
    assert clinical_advice(text)


@pytest.mark.parametrize(
    "text",
    [
        "Start a retro authorization review for CLM-900001",
        "Ask Pine Hill Orthopedics for clinical notes on PA-500101",
        "Your MRI, knee, left needed a prior authorization.",
        "It should take about 10 business days.",
    ],
)
def test_billing_language_is_not_clinical_advice(text):
    assert clinical_advice(text) is None


def _pack_with_script(script: str) -> dict[str, Pack]:
    data = yaml.safe_load((PACKS_DIR / "claims_status.yaml").read_text())
    data["next_best_steps"][0]["script"] = script
    return {"claims_status": Pack.model_validate(data)}


async def test_rule_with_clinical_advice_is_never_sent():
    packs = _pack_with_script("You should take ibuprofen for {claims.service} until it is fixed.")
    h = Harness(packs)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    await h.say("caller", "It was denied, no prior authorization.")
    assert h.heroes() == []
    assert any(k == "guardrail" for k, _, _ in h.events)


async def test_all_shipped_rules_pass_the_guardrail(packs):
    for p in packs.values():
        for r in p.next_best_steps:
            for text in (r.headline, r.why, r.script):
                assert clinical_advice(text) is None, (p.id, r.id, text)


# --- the assist LLM ------------------------------------------------------------------------


async def test_llm_proposal_is_rendered_from_the_record_not_the_llm_text(packs):
    llm = FakeLLM(
        {
            "next_steps": [
                {
                    "rule_id": "retro_auth_review",
                    "records": [{"connector": "claims", "id": "CLM-900001"}],
                    "headline": "Tell her to take two aspirin",
                }
            ],
            "cards": [{"template_id": "claims_article", "ids": ["KB-CL-002"]}],
        }
    )
    h = Harness(packs, llm=llm, rows=default_rows())
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    # No rule trigger in this line, so only the LLM proposes; "prior auth" was heard before.
    h.engine.window_add_for_test("caller", "Nobody told me about prior auth.")
    await h.say("caller", "So what can we do about it?")
    await h.engine.llm_pass()
    hero = h.engine.hero
    assert hero.rule_id == "retro_auth_review"
    assert hero.body["headline"] == "Start a retro authorization review for CLM-900001"
    assert "aspirin" not in str(hero.to_dict())
    assert ("claims_get", {"claim_id": "CLM-900001"}) in h.connectors.calls
    assert hero.trigger.phrase == "prior auth"
    article = next(c for c in h.added() if c["kind"] == "article")
    assert article["id"] == "kb:KB-CL-002" and article["body"]["rows"][0]["article_id"] == (
        "KB-CL-002"
    )
    assert article["source"]["records"] == [{"connector": "kb_articles", "id": "KB-CL-002"}]


async def test_llm_is_not_called_before_any_trigger_is_heard(packs):
    # PRD step 3: the assist looks things up when a trigger fires, not on every line.
    llm = FakeLLM({"next_steps": [], "cards": [{"template_id": "claims_table", "ids": []}]})
    h = Harness(packs, llm=llm)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    await h.say("agent", "Thanks for holding, Maria.")
    await h.engine.llm_pass()
    assert llm.requests == [] and h.added() == []


async def test_fallback_pack_lets_the_llm_answer_any_question(packs):
    llm = FakeLLM(
        {"next_steps": [], "cards": [{"template_id": "kb_article", "ids": ["KB-CL-010"]}]}
    )
    h = Harness(packs, llm=llm)
    await h.engine.start({"reason": "a question about my paperwork"}, "general_question")
    assert h.engine.pack.id == "general_question"
    line = await h.say("caller", "What is this paper I got in the mail?")
    await h.engine.llm_pass()
    [card] = h.added()
    assert card["id"] == "kb:KB-CL-010" and card["trigger"]["line_id"] == line["line_id"]
    assert card["trigger"]["phrase"] is None


async def test_article_cards_show_at_most_two_articles(packs):
    rows = default_rows()
    rows["kb_articles"] += [
        dict(rows["kb_articles"][0], article_id=f"KB-CL-0{n}", title=f"Article {n}")
        for n in (20, 21, 22)
    ]
    ids = [r["article_id"] for r in rows["kb_articles"]]
    llm = FakeLLM({"next_steps": [], "cards": [{"template_id": "claims_article", "ids": ids}]})
    h = Harness(packs, llm=llm, rows=rows)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    h.engine.window_add_for_test("caller", "Why was it denied?")
    await h.engine.llm_pass()
    article = next(c for c in h.added() if c["kind"] == "article")
    assert len(article["body"]["rows"]) == assist.MAX_ARTICLES == 2


async def test_llm_request_has_window_intake_pack_and_allowed_tools(packs):
    llm = FakeLLM()
    h = Harness(packs, llm=llm)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    await h.say("caller", "Hello there, my claim.")
    await h.say("agent", "Hi Maria, how can I help?")
    await h.engine.llm_pass()
    [req] = llm.requests
    assert req.pack.id == "claims_status"
    assert [(ln["role"], ln["text"]) for ln in req.window] == [
        ("caller", "Hello there, my claim."),
        ("agent", "Hi Maria, how can I help?"),
    ]
    assert req.intake["claim_number"] == "CLM-900001"
    names = {t["name"] for t in req.tools}
    assert "claims_search" in names and "payments_search" not in names
    assert "Maria" in req.prompt() and "CALLER: Hello there, my claim." in req.prompt()


async def test_llm_cannot_call_tools_outside_the_pack(packs):
    llm = FakeLLM(tool_calls=[("payments_search", {}), ("claims_search", {"status": "denied"})])
    h = Harness(packs, llm=llm)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    h.engine.window_add_for_test("caller", "About my claim.")
    await h.engine.llm_pass()
    assert [n for n, _ in h.connectors.calls] == ["claims_search"]


@pytest.mark.parametrize(
    "proposal",
    [
        # record that does not exist
        {"rule_id": "retro_auth_review", "records": [{"connector": "claims", "id": "CLM-1"}]},
        # record exists but the rule's `when` fails (paid claim)
        {"rule_id": "retro_auth_review", "records": [{"connector": "claims", "id": "CLM-900002"}]},
        # rule not in the active pack
        {
            "rule_id": "duplicate_premium_refund",
            "records": [{"connector": "payments", "id": "PAY-000064"}],
        },
        # connector not allowed by the pack
        {"rule_id": "retro_auth_review", "records": [{"connector": "payments", "id": "PAY-1"}]},
        # garbage
        {"rule": "x"},
    ],
)
async def test_bad_llm_proposals_are_dropped(packs, proposal):
    h = Harness(packs, llm=FakeLLM({"next_steps": [proposal], "cards": []}))
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    h.engine.window_add_for_test("caller", "It was a denial about prior authorization.")
    await h.engine.llm_pass()
    assert h.heroes() == []


async def test_llm_rule_without_its_trigger_heard_is_dropped(packs):
    proposal = {
        "rule_id": "retro_auth_review",
        "records": [{"connector": "claims", "id": "CLM-900001"}],
    }
    h = Harness(packs, llm=FakeLLM({"next_steps": [proposal], "cards": []}))
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    await h.say("caller", "Hello, I have a question.")
    await h.engine.llm_pass()
    assert h.heroes() == []


async def test_llm_failure_reports_status_and_keeps_listening(packs):
    llm = FailingLLM()
    h = Harness(packs, llm=llm)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    await h.say("caller", "It was denied.")
    await h.engine.llm_pass()
    assert llm.calls == 1
    assert h.heroes()  # the trigger path still worked
    assert h.events_of("assist_status")[-1]["state"] in ("listening", "error")


async def test_llm_pass_is_debounced(packs):
    llm = FakeLLM()
    h = Harness(packs, llm=llm)
    h.engine.llm_debounce_secs = 0.05
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    for text in ("One.", "Two, about my claim.", "Three."):
        await h.say("caller", text)
    await asyncio.sleep(0.2)
    assert len(llm.requests) == 1
    await h.engine.stop()


async def test_window_keeps_the_last_lines(packs):
    h = await maria(packs)
    for i in range(assist.WINDOW_LINES + 5):
        await h.say("caller", f"line {i}")
    w = h.engine.window()
    assert len(w) == assist.WINDOW_LINES and w[-1]["text"] == f"line {assist.WINDOW_LINES + 4}"


# --- resync and listen only ------------------------------------------------------------------


async def test_resync_sends_the_current_state(packs):
    h = await maria(packs)
    await h.say("caller", "It was denied.")
    h.sent.clear()
    await h.engine.resync()
    events = [m["event"] for m in h.sent]
    assert events[:2] == ["assist_status", "plan_update"]
    assert "hero" in events and "card_added" in events
    assert not [e for e in h.events if e[0] == "card_sent" and e[2].get("resync")]


def test_engine_has_no_way_to_speak():
    src = Path(assist.__file__).read_text()
    for name in ("TTSSpeakFrame", "TTSService", "LLMMessagesAppendFrame", "queue_frames"):
        assert name not in src
