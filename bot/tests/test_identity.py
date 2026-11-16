"""Identity matching (PRD IN-6), pure logic: verified / not_verified / not_found.

Member IDs are 6 digits read in two groups of three (P5-10, Nov 16): 418 302.
"""

import datetime as dt

import pytest
from agent_assist_bot.calls import (
    Member,
    is_valid_member_id,
    match_identity,
    normalize_member_id,
)

MARIA = Member("418302", "Maria", "Lopez", dt.date(1984, 3, 12))
JAMES = Member("527194", "James", "Carter", dt.date(1979, 7, 4))
# Two members named Mark Hill (different DOB and ID): the DOB check matters.
MARK_A = Member("285916", "Mark", "Hill", dt.date(1975, 10, 6))
MARK_B = Member("396127", "Mark", "Hill", dt.date(1986, 2, 9))
MEMBERS = [MARIA, JAMES, MARK_A, MARK_B]


def check(name, dob, member_id):
    by_id = {m.member_id: m for m in MEMBERS}
    return match_identity(MEMBERS, by_id.get(normalize_member_id(member_id)), name, dob, member_id)


@pytest.mark.parametrize(
    "raw, want",
    [
        ("418302", "418302"),
        ("418 302", "418302"),
        ("418-302", "418302"),
        ("418, 302.", "418302"),
        # Spoken forms, as an LLM may pass them through.
        ("four one eight, three zero two", "418302"),
        ("Four One Eight Three Oh Two", "418302"),
        ("four eighteen, three oh two", "418302"),
        ("forty one eight three zero two", "418302"),
        ("four one eight 302", "418302"),
        ("", ""),
        ("unknown", ""),
    ],
)
def test_normalize_member_id(raw, want):
    assert normalize_member_id(raw) == want


@pytest.mark.parametrize(
    "raw, ok",
    [
        ("418302", True),
        ("418 302", True),
        ("four one eight, three zero two", True),
        ("41830", False),  # 5 digits
        ("4183021", False),  # 7 digits
        ("EVM100231", False),  # the old format
        ("", False),
    ],
)
def test_is_valid_member_id(raw, ok):
    assert is_valid_member_id(raw) is ok


def test_all_three_match_is_verified():
    r = check("Maria Lopez", "1984-03-12", "418302")
    assert (r.result, r.member_id) == ("verified", "418302")


def test_spoken_member_id_still_verifies():
    r = check("Maria Lopez", "1984-03-12", "four one eight, three zero two")
    assert (r.result, r.member_id) == ("verified", "418302")


def test_name_match_ignores_case_punctuation_and_middle_names():
    assert check("maria  j. LOPEZ", "1984-03-12", "418 302").result == "verified"


def test_wrong_dob_is_not_verified_and_keeps_member_id():
    r = check("Maria Lopez", "1984-03-21", "418302")
    assert (r.result, r.member_id) == ("not_verified", "418302")


def test_wrong_name_is_not_verified():
    assert check("Linda Nguyen", "1984-03-12", "418302").result == "not_verified"


def test_unparseable_dob_is_not_verified():
    assert check("Maria Lopez", "March-ish", "418302").result == "not_verified"


def test_unknown_member_id_but_name_and_dob_match_one_member_is_not_verified():
    r = check("Mark Hill", "1986-02-09", "999999")
    assert (r.result, r.member_id) == ("not_verified", "396127")


def test_same_name_different_dob_resolves_to_neither():
    # Two Mark Hills: name alone never identifies.
    r = check("Mark Hill", "1990-01-01", "999999")
    assert (r.result, r.member_id) == ("not_found", None)


def test_mark_hill_dob_picks_the_right_twin():
    a = check("Mark Hill", "1975-10-06", "285916")
    b = check("Mark Hill", "1986-02-09", "396127")
    assert (a.result, a.member_id) == ("verified", "285916")
    assert (b.result, b.member_id) == ("verified", "396127")
    # The other twin's DOB with this ID: not verified.
    assert check("Mark Hill", "1986-02-09", "285916").result == "not_verified"


def test_nothing_matches_is_not_found():
    r = check("Nobody Here", "2000-01-01", "618205")
    assert (r.result, r.member_id) == ("not_found", None)


# AP-18: the agent sees "Identity n of 3", so the check records which details matched.
@pytest.mark.parametrize(
    "name, dob, member_id, want",
    [
        ("Maria Lopez", "1984-03-12", "418302", ("full_name", "dob", "member_id")),
        ("Maria Lopez", "1984-03-21", "418302", ("full_name", "member_id")),
        ("Linda Nguyen", "1984-03-12", "418302", ("dob", "member_id")),
        ("Linda Nguyen", "2001-01-01", "418302", ("member_id",)),
        ("Mark Hill", "1986-02-09", "999999", ("full_name", "dob")),
        ("Mark Hill", "1990-01-01", "999999", ()),
        ("Nobody Here", "2000-01-01", "618205", ()),
    ],
)
def test_check_records_which_details_matched(name, dob, member_id, want):
    assert check(name, dob, member_id).checks == want


# ---- close match (IN-6 v2, Chris Nov 16) ---------------------------------------------

LINDA = Member("741526", "Linda", "Nguyen", dt.date(1958, 4, 22))
AISHA = Member("174839", "Aisha", "Patel", dt.date(1995, 8, 9))
BEN = Member("852617", "Ben", "Cole", dt.date(1990, 5, 17))
PERSONAS = [MARIA, JAMES, MARK_A, MARK_B, LINDA, AISHA, BEN]


def check_p(name, dob, member_id):
    by_id = {m.member_id: m for m in PERSONAS}
    return match_identity(PERSONAS, by_id.get(normalize_member_id(member_id)), name, dob, member_id)


@pytest.mark.parametrize(
    "heard, member",
    [
        # What STT heard in the identity spike (docs/spikes/identity.md).
        ("Linda Yuen", LINDA),  # last name similarity 0.50, 3 edits
        ("Linda Hengguyen", LINDA),  # 0.67
        ("Ayesha Patel", AISHA),  # first name 0.67
        ("Ben Coal", BEN),  # 0.50
        ("Maria Lopes", MARIA),
    ],
)
def test_close_name_with_exact_id_and_dob_is_close_match(heard, member):
    r = check_p(heard, member.dob.isoformat(), member.member_id)
    assert (r.result, r.member_id) == ("close_match", member.member_id)
    assert r.checks == ("dob", "member_id")
    assert r.on_file_name == f"{member.first_name} {member.last_name}"


@pytest.mark.parametrize(
    "heard, member",
    [
        ("Linda Wayne", LINDA),  # 0.17
        ("Linda Win", LINDA),  # 0.17
        ("Ayesha Pattel", AISHA),  # both parts off: one must be exact
        ("Murray Lopes", MARIA),  # both parts off
        ("Maria Nguyen", MARIA),  # last name a different name
        ("Linda", LINDA),  # one word: never close
        ("Lee Nguyen", LINDA),  # first name 0.20
    ],
)
def test_name_too_far_is_not_verified(heard, member):
    r = check_p(heard, member.dob.isoformat(), member.member_id)
    assert (r.result, r.member_id) == ("not_verified", member.member_id)


def test_close_name_with_wrong_dob_is_not_verified():
    # Ben's scripted wrong DOB (May 7, not May 17) never close matches.
    r = check_p("Ben Coal", "1990-05-07", "852617")
    assert r.result == "not_verified"


def test_close_name_with_unknown_member_id_is_not_close_match():
    # The member ID must match exactly: name + DOB alone never close match.
    r = check_p("Linda Yuen", "1958-04-22", "999999")
    assert (r.result, r.member_id) == ("not_found", None)


def test_close_name_with_another_members_id_is_not_verified():
    # Linda's DOB with Aisha's member ID: the DOB is wrong for that ID.
    r = check_p("Linda Yuen", "1958-04-22", "174839")
    assert r.result == "not_verified"


def test_two_mark_hills_still_need_dob_and_member_id():
    assert check_p("Mark Hall", "1986-02-09", "396127").result == "close_match"
    assert check_p("Mark Hall", "1975-10-06", "396127").result == "not_verified"
    assert check_p("Mark Hill", "1990-01-01", "999999").result == "not_found"


def test_exact_name_is_still_verified_not_close():
    r = check_p("Linda Nguyen", "1958-04-22", "741526")
    assert r.result == "verified"
    assert r.on_file_name == "Linda Nguyen"


def test_not_found_has_no_name_on_file():
    assert check_p("Nobody Here", "2000-01-01", "618205").on_file_name is None


@pytest.mark.parametrize(
    "a, b, want",
    [
        ("nguyen", "yuen", True),
        ("aisha", "ayesha", True),
        ("okafor", "ford", False),
        ("nguyen", "wayne", False),
        ("alvarez", "nguyen", False),
        ("chen", "chan", True),
        ("cole", "coal", True),
        ("li", "lee", False),
    ],
)
def test_name_part_close(a, b, want):
    from agent_assist_bot.calls import name_part_close

    assert name_part_close(a, b) is want
