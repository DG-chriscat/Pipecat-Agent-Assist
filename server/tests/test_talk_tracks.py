"""Caller talk tracks (P5-11): every expected screen moment is real. No network.

Each demo persona has a `talk_track` (demo_personas.yaml): beats in call order (intake
to the bot, the conversation with the agent, closing), each with what the caller says,
backup lines for likely questions (`if_asked`), and what should show on the AGENT
screen (`expect`). These tests fail when an expect item names a hero, card, plan step,
KB article, or reason code that the packs or the seed do not have, when a hero's rule
would not fire or apply for that caller, or when a number in a line does not match
the seed.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

import pytest
from agent_assist_bot.assist import CALLBACK_STEP, VERIFY_CARD_ID
from agent_assist_bot.assist_ask import kb_hits, parse_ask
from agent_assist_bot.calls import spoken_digits
from agent_assist_bot.connectors.catalog import CONNECTORS
from agent_assist_bot.packs import connector_facts, load_packs, match_phrases
from agent_assist_bot.tone import tone_tip
from agent_assist_server import demo_personas
from agent_assist_server.db import seed

AS_OF = dt.date(2026, 11, 15)
DATA = seed.build_seed(AS_OF)
PACKS = load_packs()
PERSONAS = demo_personas.load()
BY_KEY = {p["key"]: p for p in PERSONAS}
KB_ROWS = [
    {"article_id": a.article_id, "title": a.title, "tags": list(a.tags)} for a in DATA.kb_articles
]
KB_IDS = {a.article_id for a in DATA.kb_articles}
TONE_HEADLINES = {tone_tip(s)["headline"] for s in (2, 3, 4)}
CONN = {c.name: c for c in CONNECTORS}
_SEED_ROWS = {
    "claims": DATA.claims,
    "payments": DATA.payments,
    "prior_authorizations": DATA.prior_authorizations,
    "referrals": DATA.referrals,
    "interactions": DATA.interactions,
}


def rows(connector: str, member_id: str) -> list[dict]:
    """The member's seed rows in the connector's field names (what the assist reads)."""
    cols = CONN[connector].columns
    out = []
    for obj in _SEED_ROWS[connector]:
        if obj.member_id != member_id:
            continue
        d = dataclasses.asdict(obj)
        out.append({f: d[c] for f, c in cols.items() if c in d})
    return out


def beats(p):
    return p["talk_track"]


def items(p, kind: str | None = None):
    for i, b in enumerate(beats(p)):
        for e in b["expect"]:
            if kind is None or e["kind"] == kind:
                yield i, b, e


def member_ids(p) -> set[str]:
    """Every record id the caller owns in the seed (claims, auths, payments, calls)."""
    out: set[str] = set()
    for conn in _SEED_ROWS:
        idf = CONN[conn].id_field
        out |= {str(r[idf]) for r in rows(conn, p["member_id"])}
    return out


ALL = pytest.mark.parametrize("p", PERSONAS, ids=lambda p: p["key"])
MEMBERS = pytest.mark.parametrize(
    "p", [p for p in PERSONAS if p["in_seed"]], ids=lambda p: p["key"]
)


# --- shape --------------------------------------------------------------------------


@ALL
def test_every_persona_has_a_talk_track_in_call_order(p):
    phases = [b["phase"] for b in beats(p)]
    assert phases[0] == "intake", p["key"]
    order = {"intake": 0, "agent": 1, "closing": 2}
    assert phases == sorted(phases, key=order.__getitem__), p["key"]
    assert all(b["to"] == "bot" for b in beats(p) if b["phase"] == "intake")
    assert all(b["to"] == "agent" for b in beats(p) if b["phase"] != "intake")
    for b in beats(p):
        assert b["say"].strip()
        assert 0 <= len(b["if_asked"]) <= 3
        for e in b["expect"]:
            assert e["kind"] in demo_personas.EXPECT_KINDS, (p["key"], e)
            assert e["text"].strip()


@ALL
def test_every_track_but_the_emergency_reaches_the_agent_and_closes(p):
    phases = {b["phase"] for b in beats(p)}
    if "emergency" in p["features"]:
        assert "agent" not in phases
        return
    assert {"intake", "agent", "closing"} <= phases, p["key"]
    assert beats(p)[-1]["phase"] == "closing"


def test_emergency_and_not_found_stay_short():
    # Intake is the same for everyone; after it, at most two beats.
    for key in ("emily_stone", "tom_reed"):
        assert len([b for b in beats(BY_KEY[key]) if b["phase"] != "intake"]) <= 2, key
    for p in PERSONAS:
        assert len(beats(p)) <= 16, p["key"]


# The bot asks the questions of the intake pack in Settings (default claims_status) for
# every caller, whatever the assist pack turns out to be.
INTAKE_STEPS = ["identity", "confirm", "reason"]
INTAKE_STEPS += [q.key for q in PACKS["claims_status"].questions] + ["readback"]


@ALL
def test_intake_beats_answer_the_bots_questions_in_order(p):
    steps = [b["step"] for b in beats(p) if b["phase"] == "intake"]
    if "emergency" in p["features"]:
        assert steps[:2] == ["identity", "confirm"] and steps[-1] == "reason", steps
        return
    assert steps == INTAKE_STEPS, p["key"]


def _switch(cur, line):
    """AssistEngine._maybe_switch_pack: a non fallback pack with more matches wins."""
    from agent_assist_bot.packs import select_pack

    best = select_pack(line, PACKS)
    if best is None or best is cur or best.triggers.fallback:
        return cur
    return best if len(best.match_triggers(line)) > len(cur.match_triggers(line)) else cur


@MEMBERS
def test_the_assist_lands_on_the_personas_pack_and_stays(p):
    from agent_assist_bot.packs import select_pack

    skip = {"identity", "confirm", "readback"}
    text = " ".join(b["say"] for b in beats(p) if b["phase"] == "intake" and b["step"] not in skip)
    if "emergency" in p["features"]:
        return
    cur = select_pack(text, PACKS)
    for b in beats(p):
        if b["phase"] == "intake":
            continue
        for line in [b["say"], *b["if_asked"]]:
            cur = _switch(cur, line)
            assert cur.id == p["pack"], (p["key"], line, cur.id)


def test_optional_beats_are_marked_and_cover_the_three_features():
    feats = {b.get("feature") for p in PERSONAS for b in beats(p) if b.get("optional")}
    assert {"tone_tip", "ask_bar", "verify"} <= feats
    for p in PERSONAS:
        for b in beats(p):
            if b.get("feature"):
                assert b.get("optional") is True, (p["key"], b["say"])
            if b.get("optional"):
                assert b["phase"] == "agent"


def test_the_script_field_still_works_and_comes_from_the_track():
    for p in PERSONAS:
        assert p["script"] == [
            {"to": b["to"], "say": b["say"], **({"note": b["note"]} if b.get("note") else {})}
            for b in beats(p)
            if not b.get("optional")
        ], p["key"]


def test_validation_rejects_a_bad_track():
    good = {"phase": "intake", "to": "bot", "say": "Hi.", "if_asked": [], "expect": []}
    demo_personas.validate_track("x", [good])
    bad_cases = [
        [],
        [{**good, "phase": "agent", "to": "agent"}],  # does not start with intake
        [good, {**good, "say": " "}],
        [good, {**good, "if_asked": ["a", "b", "c", "d"]}],
        [good, {**good, "expect": [{"kind": "hero", "text": "x"}]}],  # hero needs an id
        [good, {**good, "expect": [{"kind": "sparkle", "text": "x"}]}],
        [good, {**good, "phase": "closing", "to": "agent"}, {**good}],  # out of order
        [good, {**good, "phase": "agent", "to": "bot"}],
        [good, {**good, "phase": "agent", "to": "agent", "feature": "tone_tip"}],  # not optional
    ]
    for bad in bad_cases:
        with pytest.raises(ValueError):
            demo_personas.validate_track("x", bad)


# --- tester notes from P5-4 (#59) ------------------------------------------------------


@ALL
def test_no_line_to_the_bot_spells_a_name(p):
    # Spelled names inside a sentence were misheard in P5-4 batch 1; say names plainly.
    # (Spelling a name when the human agent asks for it is fine: an if_asked line.)
    spelled = re.compile(r"\b(?:[A-Z],? ){2,}[A-Z]\b")
    for b in beats(p):
        lines = [b["say"], *(b["if_asked"] if b["phase"] == "intake" else [])]
        for line in lines:
            assert not spelled.search(line), (p["key"], line)


def test_linda_fixes_her_name_plainly():
    fix = [ln for b in beats(BY_KEY["linda_nguyen"]) for ln in [b["say"], *b["if_asked"]]]
    assert any("No, my name is Linda Nguyen." in ln for ln in fix)


@MEMBERS
def test_closing_tells_the_agent_to_accept_the_hero_before_end(p):
    heroes = [e for _, _, e in items(p, "hero")]
    if not heroes:
        return
    closing = [b for b in beats(p) if b["phase"] == "closing"]
    assert closing, p["key"]
    note = " ".join(b.get("agent_note", "") for b in closing).lower()
    assert "accept" in note and "before end" in note, p["key"]


# --- expect items against packs and the seed -------------------------------------------


@ALL
def test_hero_items_are_real_rules_that_fire_and_apply(p):
    pack = PACKS[p["pack"]]
    agent_lines: list[str] = []
    for _, b in enumerate(beats(p)):
        if b["phase"] != "intake":
            agent_lines.append(b["say"])
        for e in b["expect"]:
            if e["kind"] != "hero":
                continue
            if e["id"] == VERIFY_CARD_ID:
                assert {"close_match", "not_verified", "not_found"} & set(p["features"])
                continue
            prefix, rule_id, rid = e["id"].split(":", 2)
            assert prefix == "nbs"
            rule = pack.rule(rule_id)
            # Some caller line up to here says one of the rule's trigger words.
            assert any(match_phrases(t, rule.triggers) for t in agent_lines), (p["key"], e)
            conn = next(f.split(".", 1)[0] for f in rule.required_facts)
            row = next(r for r in rows(conn, p["member_id"]) if str(r[CONN[conn].id_field]) == rid)
            facts = connector_facts(conn, row)
            assert rule.applies(facts), (p["key"], e)
            assert rule.render(facts).headline in e["text"], (p["key"], e["text"])


@ALL
def test_card_items_are_pack_templates_with_rows_for_this_caller(p):
    pack = PACKS[p["pack"]]
    for _, _, e in items(p, "card"):
        parts = e["id"].split(":")
        tpl = pack.card_template(parts[1])
        mine = rows(tpl.connector, p["member_id"])
        if parts[0] == "alert":
            assert tpl.type == "alert"
            idf = CONN[tpl.connector].id_field
            assert [str(r[idf]) for r in tpl.detect.find(mine)] == parts[2].split("+")
        else:
            assert parts[0] == "card" and tpl.type == "record", e
            hits = [r for r in mine if all(str(r[k]) == v for k, v in tpl.search.items())]
            assert hits, (p["key"], e)


@ALL
def test_kb_items_exist_and_would_show(p):
    pack = PACKS[p["pack"]]
    agent_lines = [b["say"] for b in beats(p) if b["phase"] != "intake"]
    for _, _, e in items(p, "kb"):
        assert e["id"] in KB_IDS, e
        if e.get("via") == "hero_source":
            assert any(e["id"] in r.source for r in pack.next_best_steps), e
        elif e.get("via") != "llm":
            # The general question pack shows an article the caller's question names.
            assert pack.triggers.fallback, (p["key"], e)
            assert any(
                (h := kb_hits(line, KB_ROWS)) and h[0].row["article_id"] == e["id"]
                for line in agent_lines
            ), (p["key"], e)
    for kb in p["kb_articles"]:
        assert kb in KB_IDS


@ALL
def test_plan_items_are_steps_in_the_pack(p):
    pack = PACKS[p["pack"]]
    steps = {s.id: s for s in pack.call_plan}
    heroes = {e["id"].split(":")[1] for _, _, e in items(p, "hero") if e["id"].startswith("nbs:")}
    actions = {pack.rule(r).action.id for r in heroes}
    for _, _, e in items(p, "plan"):
        if e["id"] == CALLBACK_STEP["id"]:
            assert {"not_verified", "not_found"} & set(p["features"]), p["key"]
            continue
        step = steps[e["id"]]
        if e.get("via") == "agent_words":
            # Ticked when the agent says one of the step's phrases.
            assert e.get("phrase") in step.done_when.phrases, (p["key"], e)
        else:
            # Ticked by the accepted hero's action.
            assert set(step.done_when.actions) & actions, (p["key"], e)


@ALL
def test_ask_items_are_answerable_from_this_pack(p):
    pack = PACKS[p["pack"]]
    for _, b, e in items(p, "ask"):
        assert b.get("optional") and b.get("feature") == "ask_bar", (p["key"], e)
        q = parse_ask(e["ask"])
        if q is None:
            hits = kb_hits(e["ask"], KB_ROWS)
            assert hits and hits[0].row["article_id"] == e["id"], (p["key"], e)
            assert "kb_articles" in pack.allowed_connectors
            continue
        assert q.connector in pack.allowed_connectors, (p["key"], e)
        mine = rows(q.connector, p["member_id"])
        mine = [r for r in mine if all(str(r.get(k)) == v for k, v in q.filters.items())]
        idf = CONN[q.connector].id_field
        assert e["id"] in [str(r[idf]) for r in mine[: q.limit]], (p["key"], e)


@ALL
def test_tone_tip_items_name_a_real_tip(p):
    for _, b, e in items(p, "tone_tip"):
        assert b.get("feature") == "tone_tip"
        assert any(h in e["text"] for h in TONE_HEADLINES), e


@ALL
def test_repeat_caller_items_match_the_interaction_history(p):
    since = dt.datetime.combine(AS_OF - dt.timedelta(days=30), dt.time(), tzinfo=dt.UTC)
    calls = [r for r in rows("interactions", p["member_id"]) if r["started_at"] >= since]
    said = any(True for _ in items(p, "repeat_caller"))
    assert said == (len(calls) >= 2), (p["key"], len(calls))
    assert said == ("repeat_caller" in p["features"])


@ALL
def test_identity_and_verify_items_match_the_case(p):
    kinds = {e["kind"] for _, _, e in items(p)}
    gated = {"close_match", "not_verified", "not_found"} & set(p["features"])
    if {"not_verified", "not_found"} & set(p["features"]):
        assert "verify" in kinds and "locked" in kinds, p["key"]
    if "verify" in kinds or "locked" in kinds:
        assert gated, p["key"]
    if "close_match" in p["features"]:
        verify = [b for _, b, e in items(p, "verify")]
        assert verify and all(b.get("optional") for b in verify), p["key"]


@ALL
def test_wrap_up_items_use_pack_codes_and_the_callers_records(p):
    pack = PACKS[p["pack"]]
    codes = {c.code: c for c in pack.wrap_up.reason_codes}
    heroes = [e for _, _, e in items(p, "hero") if e["id"].startswith("nbs:")]
    wraps = [e for _, b, e in items(p, "wrap_up")]
    if "emergency" in p["features"]:
        assert not wraps
        return
    assert len(wraps) == 1 and beats(p)[-1]["expect"][-1] is wraps[0], p["key"]
    w = wraps[0]
    assert w["reason_code"] in codes
    if heroes and w.get("via") != "llm":
        action = pack.rule(heroes[-1]["id"].split(":")[1]).action.id
        assert action in codes[w["reason_code"]].actions, (p["key"], w)
        assert heroes[-1]["id"].split(":")[2] in w["refs"]
    for ref in w.get("refs", []):
        assert ref in member_ids(p), (p["key"], ref)
        assert ref in w["text"]


# --- numbers in every line match the seed ----------------------------------------------

_ID = re.compile(r"\b(?:PA|PAY|INT|REF)-\d+\b|(?<![\w-])\d{5}(?![\w-])")
_AMOUNT = re.compile(r"\$(\d[\d,]*(?:\.\d\d)?)")
_AGO = re.compile(
    r"\b(?:about |almost |nearly )?(a|one|two|three|four|five|six|seven|eight|\d+) "
    r"(week|month)s? ago\b",
    re.IGNORECASE,
)
_N = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_N |= {"eight": 8}


def lines_of(p):
    for b in beats(p):
        yield b["say"]
        yield from b["if_asked"]
        for e in b["expect"]:
            yield e["text"]


def amounts(p) -> set[float]:
    plans = {pl.plan_id: pl for pl in DATA.plans}
    out = {float(r["amount"]) for r in rows("payments", p["member_id"])}
    for r in rows("claims", p["member_id"]):
        out |= {
            float(r[k])
            for k in ("billed_amount", "allowed_amount", "member_responsibility")
            if r.get(k) is not None
        }
    if p["plan_id"] in plans:
        pl = plans[p["plan_id"]]
        out |= {
            pl.deductible,
            pl.out_of_pocket_max,
            pl.pcp_copay,
            pl.specialist_copay,
            pl.monthly_premium,
        }
    return out


@ALL
def test_record_ids_in_lines_are_the_callers(p):
    mine = member_ids(p)
    for line in lines_of(p):
        for tok in _ID.findall(line):
            assert tok in mine, (p["key"], tok, line)


@ALL
def test_amounts_in_lines_match_the_seed(p):
    have = amounts(p)
    for line in lines_of(p):
        for m in _AMOUNT.findall(line):
            assert float(m.replace(",", "")) in have, (p["key"], m, line)


@ALL
def test_spoken_ids_match_the_seed(p):
    claims = {r["claim_id"] for r in rows("claims", p["member_id"])}
    for b in beats(p):
        for line in [b["say"], *b["if_asked"]]:
            if m := re.search(r"member ID is ([a-z ,]+)", line):
                assert spoken_digits(m.group(1)) == p["member_id"], (p["key"], line)
            if m := re.search(r"claim number is ([a-z ,]+)", line):
                assert spoken_digits(m.group(1)) in claims, (p["key"], line)


@ALL
def test_identity_beat_gives_the_personas_details(p):
    first = beats(p)[0]["say"]
    assert p["name"] in first and p["dob_said"] in first, p["key"]


@ALL
def test_how_long_ago_matches_the_service_date(p):
    claims = rows("claims", p["member_id"])
    target = [c for c in claims if c["claim_id"] == p.get("claim_id")] or claims
    for line in lines_of(p):
        for n, unit in _AGO.findall(line):
            days = (int(n) if n.isdigit() else _N[n.lower()]) * (7 if unit == "week" else 30)
            ago = [(AS_OF - c["service_date"]).days for c in target]
            assert any(abs(days - a) <= (7 if unit == "week" else 10) for a in ago), (
                p["key"],
                line,
                ago,
            )


@ALL
def test_providers_and_services_in_lines_are_the_callers(p):
    mine = {r["provider"] for r in rows("claims", p["member_id"])}
    mine |= {r["provider"] for r in rows("prior_authorizations", p["member_id"])}
    known = set(seed._PROVIDERS)
    for line in lines_of(p):
        for prov in known:
            if prov in line and p["in_seed"]:
                assert prov in mine, (p["key"], prov, line)
