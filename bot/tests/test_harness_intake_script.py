import pytest
from harness import intake_script as harness_script
from harness.intake_script import MARIA_LOPEZ, PERSONAS, next_line, persona

MARIA = persona("maria_lopez")
SAVED_RIGHT = {"full_name": "Maria Lopez", "dob": "1984-03-12", "member_id": "418302"}


def test_next_line_starts_with_identity():
    assert next_line(None, None) == "identity"
    assert next_line({}, None) == "identity"


def test_next_line_follows_the_first_missing_field():
    assert next_line(SAVED_RIGHT, "verified") == "reason"
    saved = {**SAVED_RIGHT, "reason": "y", "claim_number": "70412"}
    assert next_line(saved, "verified") == "service_date"


def test_next_line_reads_back_once_every_field_is_saved():
    saved = {**SAVED_RIGHT, **dict.fromkeys(["reason", "claim_number", "service_date"], "x")}
    saved["claim_concern"] = "x"
    assert next_line(saved, "verified") == "readback"


def test_script_has_a_line_for_every_step():
    steps = {next_line(None, None), "reason", "claim_number", "service_date", "claim_concern"}
    steps |= {"readback", "confirm_yes", "fix_member_id", "fix_name", "fix_both"}
    assert steps <= set(MARIA_LOPEZ)
    for p in PERSONAS:
        assert steps <= set(p.lines), p.key


# ---- member ID read back (identity saved, not yet checked) ---------------------------


def test_confirms_a_correct_read_back():
    assert next_line(SAVED_RIGHT, None) == "confirm_yes"


def test_corrects_a_misheard_member_id():
    assert next_line({**SAVED_RIGHT, "member_id": "41832"}, None) == "fix_member_id"


def test_corrects_a_misheard_name():
    assert next_line({**SAVED_RIGHT, "full_name": "Marie Lopez"}, None) == "fix_name"


def test_corrects_both_when_both_are_wrong():
    saved = {**SAVED_RIGHT, "full_name": "Marie Lopez", "member_id": "418332"}
    assert next_line(saved, None) == "fix_both"


def test_name_check_ignores_case_and_middle_names():
    assert next_line({**SAVED_RIGHT, "full_name": "maria j. LOPEZ"}, None) == "confirm_yes"


def test_member_id_check_ignores_spacing():
    assert next_line({**SAVED_RIGHT, "member_id": "four one eight, 302"}, None) == "confirm_yes"


# ---- the demo personas (docs/db.md) ------------------------------------------------------


def test_demo_personas_match_the_seed():
    from agent_assist_server.db import seed

    members = {m.member_id: m for m in seed.build_seed().members}
    for p in PERSONAS:
        m = members[p.member_id]
        assert (p.full_name, p.dob) == (f"{m.first_name} {m.last_name}", m.dob.isoformat())


def test_claim_numbers_match_the_seed():
    from agent_assist_server.db import seed

    claims = {c.claim_id: c.member_id for c in seed.build_seed().claims}
    for p in PERSONAS:
        if p.claim_number:
            assert claims[p.claim_number] == p.member_id, p.key
            assert harness_script.say_digits(p.claim_number, split=2) in p.lines["claim_number"]


def test_harness_covers_easy_names_and_linda():
    keys = {p.key for p in PERSONAS}
    assert {"maria_lopez", "james_carter", "sarah_miller", "mark_hill", "linda_nguyen"} <= keys


def test_claim_heard_right():
    assert harness_script.claim_heard_right("seven zero, four one two", "70412")
    assert harness_script.claim_heard_right("Claim 70412.", "70412")
    assert not harness_script.claim_heard_right("70 41", "70412")
    assert not harness_script.claim_heard_right("anything", "")


def test_ben_cole_gives_a_wrong_dob_and_expects_not_verified():
    daniel = persona("ben_cole")
    assert daniel.expected_identity == "not_verified"
    assert "seventh" in daniel.lines["identity"]  # May 7, not May 17
    assert all(p.expected_identity == "verified" for p in PERSONAS if p is not daniel)


def test_unknown_persona_raises():
    with pytest.raises(KeyError):
        persona("nobody")


def test_member_id_spoken_in_groups():
    assert "four one eight, three zero two" in MARIA.lines["identity"]
    assert "eight five two, six one seven" in persona("ben_cole").lines["identity"]
