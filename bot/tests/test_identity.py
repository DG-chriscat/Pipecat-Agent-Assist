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
