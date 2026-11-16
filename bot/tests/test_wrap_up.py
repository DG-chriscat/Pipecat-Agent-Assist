"""Wrap up draft (P5-1, AP-9): summary and reason code from the call, fake LLM.

The drafter sees the transcript, the intake answers (no date of birth, no member id),
the accepted actions with their records, and the call plan. The LLM picks the reason
code from the active pack only; the engine checks the reply and falls back to a
summary it writes itself from the same facts.
"""

from __future__ import annotations

import json

import pytest
from agent_assist_bot import wrap_up
from agent_assist_bot.packs import load_pack
from agent_assist_bot.wrap_up import (
    AcceptedAction,
    PlanStepState,
    WrapUpDrafter,
    WrapUpInputs,
    fallback_summary,
    reference_numbers,
    suggest_reason_code,
)


class FakeService:
    def __init__(self, *replies: str | None, error: Exception | None = None):
        self.replies = list(replies)
        self.error = error
        self.calls: list[dict] = []

    async def run_inference(
        self, context, max_tokens=None, system_instruction=None, response_schema=None
    ):
        self.calls.append(
            {
                "messages": [dict(m) for m in context.get_messages()],
                "system": system_instruction,
                "schema": response_schema,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return self.replies.pop(0)


RETRO = AcceptedAction(
    id="request_retro_auth_review",
    label="Request retro auth review",
    records=("CLM-900001",),
)


def maria(**over) -> WrapUpInputs:
    base = dict(
        pack=load_pack("claims_status"),
        intake={
            "full_name": "Maria Alvarez",
            "dob": "1984-03-12",
            "member_id": "EVM100231",
            "reason": "MRI claim was denied",
            "summary": "Maria Alvarez is calling about a denied MRI claim.",
        },
        transcript=[
            {"role": "caller", "text": "My MRI claim was denied and I don't know why."},
            {"role": "agent", "text": "I can see claim CLM-900001. It was denied because"},
            {"role": "agent", "text": "no prior authorization was on file."},
            {"role": "caller", "text": "So what do I do now?"},
            {"role": "agent", "text": "I'll start a retro authorization review today."},
        ],
        accepted=[RETRO],
        plan=[
            PlanStepState("confirm_claim", "Confirm which claim the caller means", True),
            PlanStepState("explain_status", "Explain the status or the denial reason", True),
            PlanStepState("take_next_step", "Take the next step", True),
            PlanStepState("set_expectations", "Tell the caller what happens next", False),
        ],
    )
    base.update(over)
    return WrapUpInputs(**base)


GOOD_SUMMARY = (
    "Maria Alvarez called about her denied MRI claim CLM-900001. "
    "The agent explained it was denied CO-197 because no prior authorization was on file. "
    "The agent requested a retro authorization review for CLM-900001. "
    "Maria was told the claim is reprocessed if the review is approved."
)


def reply(summary=GOOD_SUMMARY, code="appeal_started") -> str:
    return json.dumps({"summary": summary, "reason_code": code})


# ---- inputs ----


async def test_prompt_has_transcript_intake_actions_and_plan():
    svc = FakeService(reply())
    await WrapUpDrafter(svc).draft(maria())
    prompt = svc.calls[0]["messages"][0]["content"]
    assert "CALLER: My MRI claim was denied and I don't know why." in prompt
    assert "AGENT: I'll start a retro authorization review today." in prompt
    assert "reason: MRI claim was denied" in prompt
    assert "Request retro auth review (CLM-900001)" in prompt
    assert "[x] Confirm which claim the caller means" in prompt
    assert "[ ] Tell the caller what happens next" in prompt
    assert "claims_status" in prompt


async def test_prompt_never_carries_date_of_birth_or_member_id():
    svc = FakeService(reply())
    await WrapUpDrafter(svc).draft(maria())
    sent = json.dumps(svc.calls[0]["messages"])
    assert "1984-03-12" not in sent
    assert "EVM100231" not in sent


async def test_reason_code_is_constrained_to_the_pack_codes():
    svc = FakeService(reply())
    await WrapUpDrafter(svc).draft(maria())
    schema = svc.calls[0]["schema"]
    codes = [c.code for c in load_pack("claims_status").wrap_up.reason_codes]
    assert schema["properties"]["reason_code"]["enum"] == codes
    assert schema["required"] == ["summary", "reason_code"]
    assert schema["additionalProperties"] is False


async def test_prompt_lists_the_codes_and_the_rules():
    svc = FakeService(reply())
    await WrapUpDrafter(svc).draft(maria())
    prompt = svc.calls[0]["messages"][0]["content"]
    assert "appeal_started: Appeal or retro review started" in prompt
    system = svc.calls[0]["system"]
    assert "3 to 5 sentences" in system
    assert "no clinical advice" in system.lower()


async def test_transcript_is_capped_to_the_latest_lines():
    lines = [{"role": "caller", "text": f"line {i}"} for i in range(500)]
    svc = FakeService(reply())
    await WrapUpDrafter(svc).draft(maria(transcript=lines))
    prompt = svc.calls[0]["messages"][0]["content"]
    assert "line 499" in prompt
    assert "line 0\n" not in prompt
    assert len(prompt) < wrap_up.MAX_TRANSCRIPT_CHARS + 4000


# ---- the LLM's choice ----


async def test_uses_the_llm_summary_and_code():
    d = await WrapUpDrafter(FakeService(reply())).draft(maria())
    assert d.summary == GOOD_SUMMARY
    assert d.reason_code == "appeal_started"
    assert d.pack_id == "claims_status"
    assert d.source == "llm"


async def test_llm_can_pick_a_different_code_than_the_rule_suggestion():
    d = await WrapUpDrafter(FakeService(reply(code="denial_explained"))).draft(maria())
    assert d.reason_code == "denial_explained"


async def test_code_not_in_the_pack_falls_back_to_the_rule_suggestion():
    d = await WrapUpDrafter(FakeService(reply(code="refund_submitted"))).draft(maria())
    assert d.reason_code == "appeal_started"
    assert d.summary == GOOD_SUMMARY


async def test_more_than_five_sentences_is_cut_to_five():
    long = GOOD_SUMMARY + " She was polite. She said thanks. She hung up."
    d = await WrapUpDrafter(FakeService(reply(summary=long))).draft(maria())
    assert wrap_up.sentence_count(d.summary) == 5
    assert d.summary.endswith("She was polite.")


async def test_fewer_than_three_sentences_uses_the_fallback():
    d = await WrapUpDrafter(FakeService(reply(summary="Maria called. Done."))).draft(maria())
    assert d.source == "fallback"
    assert 3 <= wrap_up.sentence_count(d.summary) <= 5


async def test_missing_accepted_action_reference_is_added():
    text = (
        "Maria Alvarez called about a denied MRI claim. The agent explained the denial. "
        "A retro review was requested."
    )
    d = await WrapUpDrafter(FakeService(reply(summary=text))).draft(maria())
    assert "CLM-900001" in d.summary
    assert 3 <= wrap_up.sentence_count(d.summary) <= 5


async def test_clinical_advice_in_the_summary_uses_the_fallback():
    bad = GOOD_SUMMARY + " The agent said she should stop taking ibuprofen."
    d = await WrapUpDrafter(FakeService(reply(summary=bad))).draft(maria())
    assert d.source == "fallback"
    assert "ibuprofen" not in d.summary


@pytest.mark.parametrize("raw", [None, "", "not json", json.dumps(["x"]), json.dumps({"a": 1})])
async def test_unusable_reply_uses_the_fallback(raw):
    d = await WrapUpDrafter(FakeService(raw)).draft(maria())
    assert d.source == "fallback"
    assert d.reason_code == "appeal_started"
    assert "CLM-900001" in d.summary


async def test_llm_error_uses_the_fallback():
    d = await WrapUpDrafter(FakeService(error=RuntimeError("boom"))).draft(maria())
    assert d.source == "fallback"
    assert d.reason_code == "appeal_started"


async def test_no_llm_uses_the_fallback():
    d = await WrapUpDrafter(None).draft(maria())
    assert d.source == "fallback"


async def test_summary_never_carries_dob_or_member_id_even_if_the_llm_writes_it():
    text = GOOD_SUMMARY + " Her member ID is EVM100231."
    d = await WrapUpDrafter(FakeService(reply(summary=text))).draft(maria())
    assert "EVM100231" not in d.summary
    assert "1984-03-12" not in d.summary


# ---- deterministic parts ----


async def test_partial_reference_from_misheard_intake_becomes_the_record_id():
    # Live P5-1: intake STT heard "CLM-900" for CLM-900001; the LLM copied it.
    text = (
        "The agent confirmed claim CLM-900 for Maria Alvarez. The agent explained the denial. "
        "The agent requested a retro authorization review under reference CLM-900001."
    )
    inputs = maria(intake={"full_name": "Maria Alvarez", "claim_number": "CLM 900"})
    d = await WrapUpDrafter(FakeService(reply(summary=text))).draft(inputs)
    assert "CLM-900 " not in d.summary
    assert d.summary.count("CLM-900001") == 2


async def test_prompt_says_records_win_over_intake_and_refs_come_from_the_list():
    svc = FakeService(reply())
    await WrapUpDrafter(svc).draft(maria())
    system = svc.calls[0]["system"]
    assert "speech recognition" in system
    assert "Reference numbers list" in system
    assert "Do not comment on missing" in system


def test_fix_references_leaves_unknown_and_ambiguous_ids():
    assert wrap_up.fix_references("See CLM-12 and PA-5.", ["CLM-900001"]) == "See CLM-12 and PA-5."
    assert wrap_up.fix_references("See CLM-9.", ["CLM-900001", "CLM-900005"]) == "See CLM-9."
    assert wrap_up.fix_references("See CLM-9000.", ["CLM-900001"]) == "See CLM-900001."


def test_suggest_reason_code_from_accepted_actions():
    pack = load_pack("claims_status")
    assert suggest_reason_code(pack, [RETRO]) == "appeal_started"
    net = AcceptedAction("start_network_appeal", "Start an appeal", ("CLM-900005",))
    assert suggest_reason_code(pack, [net]) == "appeal_started"


def test_suggest_reason_code_without_actions_is_the_packs_first_code():
    assert suggest_reason_code(load_pack("claims_status"), []) == "claim_status_given"
    assert suggest_reason_code(load_pack("general_question"), []) == "question_answered"


def test_suggest_reason_code_for_a_refund():
    refund = AcceptedAction("submit_premium_refund", "Submit refund request", ("PAY-000123",))
    assert suggest_reason_code(load_pack("account_payments"), [refund]) == "refund_submitted"


def test_last_matching_action_wins():
    pack = load_pack("claims_status")
    info = AcceptedAction("contact_provider_for_info", "Request info from provider", ("CLM-1",))
    assert suggest_reason_code(pack, [RETRO, info]) == "callback_needed"


def test_reference_numbers_from_actions_and_transcript():
    refs = reference_numbers(
        maria(transcript=[{"role": "agent", "text": "PA-500101 and KB-CL-002"}])
    )
    assert refs == ["CLM-900001", "PA-500101"]


def test_reference_numbers_skip_member_id():
    refs = reference_numbers(maria(transcript=[{"role": "caller", "text": "EVM100231"}]))
    assert "EVM100231" not in refs


def test_fallback_summary_is_three_to_five_factual_sentences():
    text = fallback_summary(maria())
    assert 3 <= wrap_up.sentence_count(text) <= 5
    assert "Maria Alvarez" in text
    assert "MRI claim was denied" in text
    assert "Request retro auth review" in text
    assert "CLM-900001" in text
    assert "EVM100231" not in text


def test_fallback_summary_with_no_actions_says_so():
    text = fallback_summary(maria(accepted=[], transcript=[]))
    assert "No actions were taken" in text
    assert 3 <= wrap_up.sentence_count(text) <= 5


# ---- reporter: draft at call end, post to the server ----


async def test_reporter_drafts_and_posts_once():
    posted = []

    async def post(session_id, body):
        posted.append((session_id, body))

    r = wrap_up.WrapUpReporter(lambda: WrapUpDrafter(FakeService(reply())), post)
    r.start("s1", maria())
    r.start("s1", maria())  # second end path: ignored
    await r.wait()
    assert posted == [
        (
            "s1",
            {
                "summary": GOOD_SUMMARY,
                "reason_code": "appeal_started",
                "pack_id": "claims_status",
                "source": "llm",
            },
        )
    ]


async def test_reporter_survives_a_failing_post():
    async def post(session_id, body):
        raise RuntimeError("down")

    r = wrap_up.WrapUpReporter(lambda: WrapUpDrafter(None), post)
    r.start("s1", maria())
    await r.wait()  # no raise


async def test_reporter_wait_without_start_returns():
    async def post(session_id, body):
        raise AssertionError("not called")

    await wrap_up.WrapUpReporter(lambda: WrapUpDrafter(None), post).wait()
