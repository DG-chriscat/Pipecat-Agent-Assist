"""Use case pack loader (PRD IN-4): intake prompts and questions live in YAML."""

from pathlib import Path

import pytest
from agent_assist_bot.packs import PACKS_DIR, PackError, load_pack

CLAIMS = PACKS_DIR / "claims_status.yaml"

MINIMAL = """
id: tiny
name: Tiny pack
identity:
  prompt: Ask for name, date of birth, and member ID.
reason:
  prompt: Ask why they called.
questions:
  - key: claim_number
    ask: Do you have the claim number?
    description: The claim number, or "unknown".
readback:
  prompt: Read back the summary.
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "pack.yaml"
    p.write_text(text)
    return p


def test_claims_status_pack_ships_in_repo():
    assert CLAIMS.is_file()


def test_claims_status_pack_loads_with_questions():
    pack = load_pack("claims_status")
    assert pack.id == "claims_status"
    assert pack.identity.prompt and pack.reason.prompt and pack.readback.prompt
    # Identity card: the final read back stays short (2 minute target) and does not
    # repeat the claim number; the member ID was confirmed at the identity read back.
    assert "claim number" in pack.readback.prompt and "Do not" in pack.readback.prompt
    keys = [q.key for q in pack.questions]
    assert keys, "claims_status needs at least one use case question"
    assert len(keys) == len(set(keys))
    for q in pack.questions:
        assert q.ask.strip() and q.description.strip()


def test_load_pack_by_path(tmp_path):
    pack = load_pack(_write(tmp_path, MINIMAL))
    assert pack.id == "tiny"
    assert [q.key for q in pack.questions] == ["claim_number"]
    assert pack.questions[0].required is False


def test_question_text_changes_without_code(tmp_path):
    # IN-4: a builder edits YAML only.
    pack = load_pack(_write(tmp_path, MINIMAL.replace("Do you have", "Could you share")))
    assert pack.questions[0].ask == "Could you share the claim number?"


def test_unknown_pack_name_raises():
    with pytest.raises(PackError, match="no_such_pack"):
        load_pack("no_such_pack")


@pytest.mark.parametrize(
    "bad, match",
    [
        (MINIMAL.replace("key: claim_number", "key: Claim Number"), "key"),
        (MINIMAL.replace("  - key: claim_number", "  - key: reason"), "reserved"),
        (MINIMAL.replace("readback:\n  prompt: Read back the summary.\n", ""), "readback"),
        ("just a string", "mapping"),
    ],
    ids=["bad-key", "reserved-key", "missing-step", "not-a-mapping"],
)
def test_invalid_pack_rejected(tmp_path, bad, match):
    with pytest.raises(PackError, match=match):
        load_pack(_write(tmp_path, bad))


def test_duplicate_question_keys_rejected(tmp_path):
    text = MINIMAL.replace(
        "readback:",
        "  - key: claim_number\n    ask: Again?\n    description: dup\nreadback:",
    )
    with pytest.raises(PackError, match="duplicate"):
        load_pack(_write(tmp_path, text))


# ---- wrap up reason codes (P3-5, AP-9) -------------------------------------------


def test_claims_status_pack_has_wrap_up_reason_codes():
    pack = load_pack("claims_status")
    codes = [c.code for c in pack.wrap_up.reason_codes]
    assert len(codes) >= 3
    assert "other" in codes
    assert all(c.label and c.label[0].isupper() for c in pack.wrap_up.reason_codes)


def test_pack_without_wrap_up_gets_a_default_other_code(tmp_path):
    pack = load_pack(_write(tmp_path, MINIMAL))
    assert [(c.code, c.label) for c in pack.wrap_up.reason_codes] == [("other", "Other")]


def test_wrap_up_codes_come_from_the_pack(tmp_path):
    text = MINIMAL + "wrap_up:\n  reason_codes:\n    - {code: resolved, label: Resolved}\n"
    pack = load_pack(_write(tmp_path, text))
    assert [c.code for c in pack.wrap_up.reason_codes] == ["resolved"]
    assert pack.wrap_up.has_code("resolved")
    assert not pack.wrap_up.has_code("other")


@pytest.mark.parametrize(
    "codes, match",
    [
        ("[]", "at least 1"),
        ("[{code: Bad Code, label: x}]", "snake_case"),
        ("[{code: a, label: A}, {code: a, label: B}]", "duplicate"),
    ],
)
def test_invalid_wrap_up_codes_rejected(tmp_path, codes, match):
    text = MINIMAL + f"wrap_up:\n  reason_codes: {codes}\n"
    with pytest.raises(PackError, match=match):
        load_pack(_write(tmp_path, text))


@pytest.mark.parametrize("pack_id", ["claims_status", "account_payments", "general_question"])
def test_every_v1_pack_has_wrap_up_codes_ending_in_other(pack_id):
    codes = [c.code for c in load_pack(pack_id).wrap_up.reason_codes]
    assert len(codes) >= 3 and codes[-1] == "other"
