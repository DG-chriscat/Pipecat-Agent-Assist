"""Identity matching (PRD IN-6), pure logic: verified / not_verified / not_found."""

import datetime as dt

import pytest
from agent_assist_bot.calls import Member, match_identity, normalize_member_id

MARIA = Member("EVM100231", "Maria", "Alvarez", dt.date(1984, 3, 12))
JAMES_A = Member("EVM100232", "James", "Chen", dt.date(1979, 7, 4))
JAMES_B = Member("EVM100236", "James", "Chen", dt.date(1986, 2, 9))
MEMBERS = [MARIA, JAMES_A, JAMES_B]


def check(name, dob, member_id):
    by_id = {m.member_id: m for m in MEMBERS}
    return match_identity(MEMBERS, by_id.get(normalize_member_id(member_id)), name, dob, member_id)


@pytest.mark.parametrize(
    "raw, want",
    [("EVM100231", "EVM100231"), ("evm 100 231", "EVM100231"), ("E-V-M-1002-31", "EVM100231")],
)
def test_normalize_member_id(raw, want):
    assert normalize_member_id(raw) == want


def test_all_three_match_is_verified():
    r = check("Maria Alvarez", "1984-03-12", "EVM100231")
    assert (r.result, r.member_id) == ("verified", "EVM100231")


def test_name_match_ignores_case_punctuation_and_middle_names():
    assert check("maria  j. ALVAREZ", "1984-03-12", "evm100231").result == "verified"


def test_wrong_dob_is_not_verified_and_keeps_member_id():
    r = check("Maria Alvarez", "1984-03-21", "EVM100231")
    assert (r.result, r.member_id) == ("not_verified", "EVM100231")


def test_wrong_name_is_not_verified():
    assert check("Linda Nguyen", "1984-03-12", "EVM100231").result == "not_verified"


def test_unparseable_dob_is_not_verified():
    assert check("Maria Alvarez", "March-ish", "EVM100231").result == "not_verified"


def test_unknown_member_id_but_name_and_dob_match_one_member_is_not_verified():
    r = check("James Chen", "1986-02-09", "EVM999999")
    assert (r.result, r.member_id) == ("not_verified", "EVM100236")


def test_same_name_different_dob_resolves_to_neither():
    # Two James Chens: name alone never identifies.
    r = check("James Chen", "1990-01-01", "EVM999999")
    assert (r.result, r.member_id) == ("not_found", None)


def test_nothing_matches_is_not_found():
    r = check("Nobody Here", "2000-01-01", "XYZ000000")
    assert (r.result, r.member_id) == ("not_found", None)


# AP-18: the agent sees "Identity n of 3", so the check records which details matched.
@pytest.mark.parametrize(
    "name, dob, member_id, want",
    [
        ("Maria Alvarez", "1984-03-12", "EVM100231", ("full_name", "dob", "member_id")),
        ("Maria Alvarez", "1984-03-21", "EVM100231", ("full_name", "member_id")),
        ("Linda Nguyen", "1984-03-12", "EVM100231", ("dob", "member_id")),
        ("Linda Nguyen", "2001-01-01", "EVM100231", ("member_id",)),
        ("James Chen", "1986-02-09", "EVM999999", ("full_name", "dob")),
        ("James Chen", "1990-01-01", "EVM999999", ()),
        ("Nobody Here", "2000-01-01", "XYZ000000", ()),
    ],
)
def test_check_records_which_details_matched(name, dob, member_id, want):
    assert check(name, dob, member_id).checks == want


# ---- close match (IN-6 v2, Chris Nov 16) ---------------------------------------------

LINDA = Member("EVM100234", "Linda", "Nguyen", dt.date(1958, 1, 30))
AISHA = Member("EVM100233", "Aisha", "Patel", dt.date(1992, 11, 28))
DANIEL = Member("EVM100235", "Daniel", "Okafor", dt.date(1990, 5, 17))
PERSONAS = [MARIA, JAMES_A, JAMES_B, LINDA, AISHA, DANIEL]


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
        ("Daniel Lokafor", DANIEL),  # 0.86
        ("Maria Alvares", MARIA),
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
        ("Daniel Luca Ford", DANIEL),  # last token "ford" vs okafor: 0.33
        ("Linda Wayne", LINDA),  # 0.17
        ("Linda Win", LINDA),  # 0.17
        ("Ayesha Pattel", AISHA),  # both parts off: one must be exact
        ("Murray Alvares", MARIA),  # both parts off
        ("Maria Nguyen", MARIA),  # last name a different name
        ("Linda", LINDA),  # one word: never close
        ("Lee Nguyen", LINDA),  # first name 0.20
    ],
)
def test_name_too_far_is_not_verified(heard, member):
    r = check_p(heard, member.dob.isoformat(), member.member_id)
    assert (r.result, r.member_id) == ("not_verified", member.member_id)


def test_close_name_with_wrong_dob_is_not_verified():
    # Daniel's scripted wrong DOB (May 7, not May 17) never close matches.
    r = check_p("Daniel Lokafor", "1990-05-07", "EVM100235")
    assert r.result == "not_verified"


def test_close_name_with_unknown_member_id_is_not_close_match():
    # The member ID must match exactly: name + DOB alone never close match.
    r = check_p("Linda Yuen", "1958-01-30", "EVM999999")
    assert (r.result, r.member_id) == ("not_found", None)


def test_close_name_with_another_members_id_is_not_verified():
    # Linda's DOB with Aisha's member ID: the DOB is wrong for that ID.
    r = check_p("Linda Yuen", "1958-01-30", "EVM100233")
    assert r.result == "not_verified"


def test_two_james_chens_still_need_dob_and_member_id():
    assert check_p("James Chan", "1986-02-09", "EVM100236").result == "close_match"
    assert check_p("James Chan", "1979-07-04", "EVM100236").result == "not_verified"
    assert check_p("James Chen", "1990-01-01", "EVM999999").result == "not_found"


def test_exact_name_is_still_verified_not_close():
    r = check_p("Linda Nguyen", "1958-01-30", "EVM100234")
    assert r.result == "verified"
    assert r.on_file_name == "Linda Nguyen"


def test_not_found_has_no_name_on_file():
    assert check_p("Nobody Here", "2000-01-01", "XYZ000000").on_file_name is None


@pytest.mark.parametrize(
    "a, b, want",
    [
        ("nguyen", "yuen", True),
        ("aisha", "ayesha", True),
        ("okafor", "ford", False),
        ("nguyen", "wayne", False),
        ("alvarez", "nguyen", False),
        ("chen", "chan", True),
        ("li", "lee", False),
    ],
)
def test_name_part_close(a, b, want):
    from agent_assist_bot.calls import name_part_close

    assert name_part_close(a, b) is want
