"""TTS read back scoring (P5-5 spike, docs/spikes/tts-flux-vs-aura.md). No network."""

import pytest
from harness import readback as rb


def test_lines_cover_every_required_entity():
    kinds = {e.kind for line in rb.LINES for e in line.entities}
    assert kinds >= {
        "member_id",
        "dob",
        "claim_number",
        "amount",
        "time",
        "address",
        "business_days",
    }


def test_identity_and_hold_lines_are_the_bots_real_lines():
    from agent_assist_bot.hold import hold_line
    from agent_assist_bot.intake import READBACK_LINE, speak_member_id

    texts = {line.key: line.text for line in rb.LINES}
    assert texts["identity_readback"] == READBACK_LINE.format(
        name="Maria Alvarez", member_id=speak_member_id("EVM100231")
    )
    assert texts["hold_update"] == hold_line(2)


def test_collapse_turns_spoken_digits_into_one_comparable_string():
    assert rb.collapse("E V M, one zero zero, two three one") == "evm100231"
    assert rb.collapse("EVM 100-231.") == "evm100231"
    assert rb.collapse("Eight Oak Street") == "8oakstreet"
    assert rb.collapse("$1,240") == "1240"


@pytest.mark.parametrize(
    ("kind", "heard", "ok"),
    [
        ("member_id", "member ID E V M 1 0 0 2 3 1. Is that right?", True),
        ("member_id", "member ID EBM 100 231", False),
        ("dob", "born March 12, 1984, calling", True),
        ("dob", "born March 12th 1984", True),
        ("dob", "born March 2, 1984", False),
        ("dob", "born on 03/12/1984", True),
        ("claim_number", "claim CLM-900001 for", True),
        ("claim_number", "claim C L M 9 0 0 0 0 1", True),
        # nova-3 smart_format writes the spoken "dash" as a word: still CLM-900001.
        ("claim_number", "claim CLM dash 900001 for", True),
        ("claim_number", "claim CLM 90001", False),
        ("amount", "for $1,240 that", True),
        ("amount", "for one thousand two hundred forty dollars", True),
        ("amount", "for one thousand two hundred and forty dollars", True),
        ("amount", "for $1,204", False),
        # Flux Hannah, run 0 of the first live run: "$1,240" spoken as "twelve forty".
        ("amount", "for $12.40 dollars which", False),
        ("amount", "for twelve forty dollars which", False),
        ("time", "at 2 PM", True),
        ("time", "at 2:00 PM", True),
        ("time", "at two p.m.", True),
        ("time", "at 2 AM", False),
        ("address", "to 8 Oak Street", True),
        ("address", "to eight Oak St.", True),
        ("address", "to 80 Oak Street", False),
        ("business_days", "in 7 business days", True),
        ("business_days", "in seven business days", True),
        ("business_days", "in 17 business days", False),
        ("place", "You're number 2 in line.", True),
        ("place", "You're number to in line", False),
    ],
)
def test_entity_matchers(kind, heard, ok):
    assert rb.MATCHERS[kind](heard) is ok


def test_score_counts_entities_heard_right_per_voice():
    results = [
        rb.LineResult("v1", "l1", 0, "x", 0.1, {"amount": True, "time": False}),
        rb.LineResult("v1", "l2", 0, "y", 0.3, {"dob": True}),
        rb.LineResult("v2", "l1", 0, "x", 0.2, {"amount": True, "time": True}),
    ]
    summary = rb.summarize(results)
    assert summary["v1"]["correct"] == 2 and summary["v1"]["total"] == 3
    assert summary["v2"]["correct"] == 2 and summary["v2"]["total"] == 2
    assert summary["v1"]["ttfa_median"] == pytest.approx(0.2)
    assert summary["v1"]["misses"] == {"l1:time": 1}


def test_rescore_recomputes_entities_from_what_was_heard():
    r = rb.LineResult("v", "summary_readback", 0, "claim CLM dash 900001 for $1,240", 0.1, {})
    (out,) = rb.rescore([r])
    assert out.entities["claim_number"] is True and out.entities["amount"] is True
    assert out.entities["dob"] is False


def test_markdown_table_has_a_row_per_voice():
    results = [rb.LineResult("aura-2-thalia-en", "l1", 0, "x", 0.25, {"amount": True})]
    table = rb.markdown_table(rb.summarize(results))
    assert "| aura-2-thalia-en | 1 / 1 (100%) |" in table
