import pytest
from harness.intake_script import MARIA_ALVAREZ, PERSONAS, next_line, persona

MARIA = persona("maria_alvarez")
SAVED_RIGHT = {"full_name": "Maria Alvarez", "dob": "1984-03-12", "member_id": "EVM100231"}


def test_next_line_starts_with_identity():
    assert next_line(None, None) == "identity"
    assert next_line({}, None) == "identity"


def test_next_line_follows_the_first_missing_field():
    assert next_line(SAVED_RIGHT, "verified") == "reason"
    saved = {**SAVED_RIGHT, "reason": "y", "claim_number": "CLM-900001"}
    assert next_line(saved, "verified") == "service_date"


def test_next_line_reads_back_once_every_field_is_saved():
    saved = {**SAVED_RIGHT, **dict.fromkeys(["reason", "claim_number", "service_date"], "x")}
    saved["claim_concern"] = "x"
    assert next_line(saved, "verified") == "readback"


def test_script_has_a_line_for_every_step():
    steps = {next_line(None, None), "reason", "claim_number", "service_date", "claim_concern"}
    steps |= {"readback", "confirm_yes", "fix_member_id", "fix_name", "fix_both"}
    assert steps <= set(MARIA_ALVAREZ)
    for p in PERSONAS:
        assert steps <= set(p.lines), p.key


# ---- member ID read back (identity saved, not yet checked) ---------------------------


def test_confirms_a_correct_read_back():
    assert next_line(SAVED_RIGHT, None) == "confirm_yes"


def test_corrects_a_misheard_member_id():
    assert next_line({**SAVED_RIGHT, "member_id": "EVM00231"}, None) == "fix_member_id"


def test_corrects_a_misheard_name():
    assert next_line({**SAVED_RIGHT, "full_name": "Marie Alvarez"}, None) == "fix_name"


def test_corrects_both_when_both_are_wrong():
    saved = {**SAVED_RIGHT, "full_name": "Marie Alvarez", "member_id": "EDM100231"}
    assert next_line(saved, None) == "fix_both"


def test_name_check_ignores_case_and_middle_names():
    assert next_line({**SAVED_RIGHT, "full_name": "maria j. ALVAREZ"}, None) == "confirm_yes"


def test_member_id_check_ignores_spacing():
    assert next_line({**SAVED_RIGHT, "member_id": "evm 100 231"}, None) == "confirm_yes"


# ---- the five demo personas (docs/db.md) ------------------------------------------------


def test_five_demo_personas_match_docs_db():
    got = {(p.full_name, p.dob, p.member_id) for p in PERSONAS}
    assert got == {
        ("Maria Alvarez", "1984-03-12", "EVM100231"),
        ("James Chen", "1979-07-04", "EVM100232"),
        ("Aisha Patel", "1992-11-28", "EVM100233"),
        ("Linda Nguyen", "1958-01-30", "EVM100234"),
        ("Daniel Okafor", "1990-05-17", "EVM100235"),
    }


def test_daniel_okafor_gives_a_wrong_dob_and_expects_not_verified():
    daniel = persona("daniel_okafor")
    assert daniel.expected_identity == "not_verified"
    assert "seventh" in daniel.lines["identity"]  # May 7, not May 17
    assert all(p.expected_identity == "verified" for p in PERSONAS if p is not daniel)


def test_unknown_persona_raises():
    with pytest.raises(KeyError):
        persona("nobody")


def test_member_id_spoken_in_groups():
    assert "E V M, one zero zero, two three one" in MARIA.lines["identity"]
    assert "E V M, one zero zero, two three five" in persona("daniel_okafor").lines["identity"]
