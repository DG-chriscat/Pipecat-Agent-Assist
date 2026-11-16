"""Packs v2 (P4-2, PRD DC-10, AP-6, AP-11, AP-12): assist sections of the use case packs."""

from pathlib import Path

import pytest
from agent_assist_bot.connectors import CONNECTORS
from agent_assist_bot.packs import (
    FALLBACK_PACK,
    PACKS_DIR,
    PackError,
    load_pack,
    load_packs,
    select_pack,
)

V1_PACKS = ("claims_status", "account_payments", "general_question")


@pytest.fixture(scope="module")
def packs():
    return load_packs()


def test_all_three_v1_packs_load(packs):
    assert set(packs) == set(V1_PACKS)
    for pid in V1_PACKS:
        assert packs[pid].id == pid
        assert (PACKS_DIR / f"{pid}.yaml").is_file()


def test_every_v1_pack_has_assist_sections(packs):
    names = {c.name for c in CONNECTORS}
    for pack in packs.values():
        assert pack.allowed_connectors and set(pack.allowed_connectors) <= names
        assert pack.card_templates
        assert pack.call_plan
        assert pack.keyterms
        assert pack.triggers.phrases or pack.triggers.fallback


def test_exactly_one_fallback_pack(packs):
    assert [p.id for p in packs.values() if p.triggers.fallback] == [FALLBACK_PACK]
    assert FALLBACK_PACK == "general_question"


def test_intake_section_still_loads_for_claims_status():
    pack = load_pack("claims_status")
    assert [q.key for q in pack.questions] == ["claim_number", "service_date", "claim_concern"]
    assert pack.identity.prompt and pack.readback.prompt


# --- trigger matching ----------------------------------------------------------------


@pytest.mark.parametrize(
    "line, pack_id",
    [
        ("Hi, where is my claim for the MRI?", "claims_status"),
        ("They said it was DENIED because of prior authorization", "claims_status"),
        ("I think I was charged twice this month", "account_payments"),
        ("there's a double charge on my account", "account_payments"),
        ("I'd like a refund please", "account_payments"),
        ("What is an EOB anyway?", "general_question"),
        ("Can you tell me how to update my address", "general_question"),
    ],
)
def test_select_pack_by_trigger(packs, line, pack_id):
    assert select_pack(line, packs).id == pack_id


def test_trigger_match_reports_phrase_and_span(packs):
    line = "So it was denied, and I was charged twice."
    matches = packs["claims_status"].match_triggers(line)
    assert [m.phrase for m in matches] == ["denied"]
    m = matches[0]
    assert line[m.start : m.end].lower() == "denied"


def test_trigger_matching_ignores_case_punctuation_and_hyphens(packs):
    # STT may render CO-197 as "co 197"; contractions and punctuation vary.
    assert packs["claims_status"].match_triggers("it says code co 197?")
    assert packs["claims_status"].match_triggers("Where's my claim")


def test_trigger_matching_uses_word_boundaries(packs):
    # A phrase matches whole words only, never inside a longer token.
    assert not packs["claims_status"].match_triggers("undeniedly strange")


# --- next best step rules (DC-10) ----------------------------------------------------


def test_claims_status_has_retro_auth_rule_for_maria(packs):
    pack = packs["claims_status"]
    rule = pack.rule("retro_auth_review")
    assert rule.action.id == "request_retro_auth_review"
    assert rule.when == {"claims.denial_code": "CO-197"}
    # Maria Alvarez seed facts (docs/db.md): CLM-900001, MRI, CO-197.
    facts = {
        "intake.full_name": "Maria Alvarez",
        "claims.claim_id": "CLM-900001",
        "claims.service": "MRI, knee, left",
        "claims.provider": "Riverside Imaging Center",
        "claims.denial_code": "CO-197",
        "claims.service_date": "2026-10-13",
    }
    assert rule.applies(facts)
    card = rule.render(facts)
    assert "CLM-900001" in card.why and "CO-197" in card.why
    assert "Riverside Imaging Center" in card.script
    assert card.action_id == "request_retro_auth_review"
    assert card.source
    assert "{" not in card.headline + card.why + card.script


def test_rule_does_not_apply_without_required_facts_or_condition(packs):
    rule = packs["claims_status"].rule("retro_auth_review")
    assert not rule.applies({"claims.claim_id": "CLM-900001"})
    facts = {f: "x" for f in rule.required_facts}
    facts["claims.denial_code"] = "CO-242"
    assert not rule.applies(facts)
    facts["claims.denial_code"] = "co-197"  # case-insensitive
    assert rule.applies(facts)


def test_rule_trigger_matching(packs):
    pack = packs["claims_status"]
    rules = pack.rules_triggered("They told me it needed a prior authorization")
    assert "retro_auth_review" in [r.id for r in rules]
    assert pack.rules_triggered("hello there") == []


def test_account_payments_refund_rule_and_duplicate_alert(packs):
    pack = packs["account_payments"]
    rule = pack.rule("duplicate_premium_refund")
    assert rule.action.id == "submit_premium_refund"
    assert [r.id for r in pack.rules_triggered("I was charged twice")] == [
        "duplicate_premium_refund"
    ]
    alert = pack.card_template("duplicate_charge")
    assert alert.type == "alert"
    assert alert.connector == "payments"
    assert alert.detect is not None


def test_general_question_has_article_card(packs):
    pack = packs["general_question"]
    assert pack.allowed_connectors == ["kb_articles"]
    assert [t.type for t in pack.card_templates] == ["article"]


def test_action_ids_unique_across_v1_packs(packs):
    for pack in packs.values():
        ids = [r.action.id for r in pack.next_best_steps]
        assert len(ids) == len(set(ids))


# --- call plan (AP-12) ----------------------------------------------------------------


def test_call_plan_ticks_from_phrase_and_action(packs):
    plan = packs["claims_status"].call_plan
    assert [s.id for s in plan][0] == "confirm_claim"
    ticks = packs["claims_status"].plan_ticks(
        transcript="Agent: I can see claim CLM-900001 was denied.",
        accepted_actions=["request_retro_auth_review"],
    )
    assert "confirm_claim" in ticks and "take_next_step" in ticks
    assert "set_expectations" not in ticks
    assert packs["claims_status"].plan_ticks(transcript="", accepted_actions=[]) == []


# --- duplicate detection helper ------------------------------------------------------


def test_duplicate_detect_finds_james_chens_second_draft(packs):
    alert = packs["account_payments"].card_template("duplicate_charge")
    rows = [
        {
            "payment_id": "PAY-000010",
            "paid_at": "2026-11-01T06:00:00+00:00",
            "amount": 610.0,
            "kind": "premium",
            "status": "posted",
        },
        {
            "payment_id": "PAY-000064",
            "paid_at": "2026-11-01T06:02:00+00:00",
            "amount": 610.0,
            "kind": "premium",
            "status": "posted",
        },
        {
            "payment_id": "PAY-000011",
            "paid_at": "2026-10-01T06:00:00+00:00",
            "amount": 610.0,
            "kind": "premium",
            "status": "posted",
        },
        {
            "payment_id": "PAY-000065",
            "paid_at": "2026-11-03T06:00:00+00:00",
            "amount": 25.0,
            "kind": "copay",
            "status": "posted",
        },
    ]
    dups = alert.detect.find(rows)
    assert [r["payment_id"] for r in dups] == ["PAY-000064"]
    assert alert.detect.find(rows[2:]) == []


# --- validation errors ---------------------------------------------------------------

BASE = """
id: tiny
name: Tiny
identity:
  prompt: Ask for name, date of birth, and member ID.
reason:
  prompt: Ask why they called.
questions:
  - key: claim_number
    ask: Claim number?
    description: The claim number.
readback:
  prompt: Read back.
triggers:
  phrases: [where is my claim]
allowed_connectors: [claims]
keyterms: [CO-197]
card_templates:
  - id: claims_table
    type: record
    title: Claims
    connector: claims
    fields: [claim_id, status]
call_plan:
  - id: confirm
    label: Confirm the claim
    done_when:
      phrases: [i can see]
      actions: [act_one]
next_best_steps:
  - id: rule_one
    triggers: [denied]
    required_facts: [intake.claim_number, claims.claim_id]
    headline: Look at {claims.claim_id}
    why: Caller gave {intake.claim_number}.
    script: Let me check {claims.claim_id}.
    action: {id: act_one, label: Do it}
    source: Claims system
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "pack.yaml"
    p.write_text(text)
    return p


def test_base_fixture_is_valid(tmp_path):
    pack = load_pack(_write(tmp_path, BASE))
    assert pack.rule("rule_one").source == "Claims system"
    # Card source label defaults to the connector's source.
    assert pack.card_template("claims_table").source == "Claims system"


def test_v1_intake_only_pack_still_loads(tmp_path):
    intake_only = BASE.split("triggers:")[0]
    pack = load_pack(_write(tmp_path, intake_only))
    assert pack.allowed_connectors == [] and pack.next_best_steps == []


@pytest.mark.parametrize(
    "old, new, match",
    [
        ("allowed_connectors: [claims]", "allowed_connectors: [claims, ledger]", "ledger"),
        (
            "connector: claims\n    fields: [claim_id, status]",
            "connector: kb_articles\n    fields: [article_id]",
            "allowed_connectors",
        ),
        ("fields: [claim_id, status]", "fields: [claim_id, colour]", "colour"),
        ("Look at {claims.claim_id}", "Look at {claims.colour}", "claims.colour"),
        ("Look at {claims.claim_id}", "Look at {intake.dob}", "required_facts"),
        (
            "[intake.claim_number, claims.claim_id]",
            "[intake.claim_number, intake.nope, claims.claim_id]",
            "nope",
        ),
        (
            "[intake.claim_number, claims.claim_id]",
            "[intake.claim_number, claims.claim_id, payments.amount]",
            "payments",
        ),
        ("actions: [act_one]", "actions: [act_two]", "act_two"),
        ("type: record", "type: hologram", "type"),
        ("    triggers: [denied]\n", "    triggers: []\n", "triggers"),
    ],
    ids=[
        "unknown-connector",
        "template-connector-not-allowed",
        "template-unknown-field",
        "rule-template-unknown-fact",
        "rule-template-fact-not-required",
        "unknown-intake-key",
        "fact-connector-not-allowed",
        "plan-unknown-action",
        "bad-card-type",
        "rule-without-triggers",
    ],
)
def test_invalid_assist_section_rejected(tmp_path, old, new, match):
    assert old in BASE
    with pytest.raises(PackError, match=match):
        load_pack(_write(tmp_path, BASE.replace(old, new)))


def test_duplicate_action_ids_rejected(tmp_path):
    second = """  - id: rule_two
    triggers: [pending]
    required_facts: [claims.claim_id]
    headline: H
    why: W
    script: S
    action: {id: act_one, label: Again}
    source: Claims system
"""
    with pytest.raises(PackError, match="act_one"):
        load_pack(_write(tmp_path, BASE + second))


@pytest.mark.parametrize(
    "dup",
    ["call_plan", "card_templates"],
)
def test_duplicate_ids_rejected(tmp_path, dup):
    text = BASE
    if dup == "call_plan":
        text = text.replace(
            "next_best_steps:",
            "  - id: confirm\n    label: Again\nnext_best_steps:",
        )
    else:
        text = text.replace(
            "call_plan:",
            "  - id: claims_table\n    type: article\n    title: X\n    connector: claims\n"
            "    fields: [claim_id]\ncall_plan:",
        )
    with pytest.raises(PackError, match="duplicate"):
        load_pack(_write(tmp_path, text))


def test_when_keys_must_be_required_facts(tmp_path):
    text = BASE.replace(
        "    source: Claims system\n",
        "    source: Claims system\n    when: {claims.status: denied}\n",
    )
    with pytest.raises(PackError, match="when"):
        load_pack(_write(tmp_path, text))


def test_load_packs_rejects_two_fallbacks(tmp_path):
    a = BASE.replace("phrases: [where is my claim]", "fallback: true")
    (tmp_path / "tiny.yaml").write_text(a)
    (tmp_path / "tiny2.yaml").write_text(a.replace("id: tiny", "id: tiny2"))
    with pytest.raises(PackError, match="fallback"):
        load_packs(tmp_path)


def test_load_packs_rejects_id_not_matching_file_name(tmp_path):
    (tmp_path / "other.yaml").write_text(BASE)
    with pytest.raises(PackError, match="file name"):
        load_packs(tmp_path)


def test_load_packs_rejects_action_ids_shared_across_packs(tmp_path):
    (tmp_path / "tiny.yaml").write_text(BASE)
    (tmp_path / "tiny2.yaml").write_text(BASE.replace("id: tiny", "id: tiny2"))
    with pytest.raises(PackError, match="act_one"):
        load_packs(tmp_path)


def test_select_pack_without_fallback_returns_none(tmp_path):
    (tmp_path / "tiny.yaml").write_text(BASE)
    packs = load_packs(tmp_path)
    assert select_pack("where is my claim", packs).id == "tiny"
    assert select_pack("hello", packs) is None


def test_keyterms_are_fixed_strings(packs):
    terms = packs["claims_status"].keyterms
    assert "CO-197" in terms and "EVM" in terms
    assert "prior authorization" in [t.lower() for t in terms]
    # Not per member: no member IDs or names in the keyterms.
    assert not any(t.startswith("EVM1") for t in terms)
    assert "Maria" not in terms


# --- docs ----------------------------------------------------------------------------


def test_docs_describe_every_pack_key_and_v1_pack():
    from agent_assist_bot.packs import CardTemplate, NextBestStep, Pack

    doc = (Path(__file__).resolve().parents[2] / "docs" / "packs.md").read_text()
    for model in (Pack, CardTemplate, NextBestStep):
        for name in model.model_fields:
            assert f"`{name}`" in doc, f"docs/packs.md does not mention `{name}`"
    for pid in V1_PACKS:
        assert f"`{pid}`" in doc


def test_docs_full_example_matches_the_shipped_pack(tmp_path):
    doc = (Path(__file__).resolve().parents[2] / "docs" / "packs.md").read_text()
    example = doc.split("## Full example")[1].split("```yaml\n")[1].split("```")[0]
    pack = load_pack(_write(tmp_path, example.replace(" ...", "")))
    shipped = load_pack("account_payments")
    for key in (
        "triggers",
        "allowed_connectors",
        "keyterms",
        "card_templates",
        "call_plan",
        "next_best_steps",
    ):
        assert getattr(pack, key) == getattr(shipped, key), key
