"""Scripted callers for the demo personas (docs/db.md, `seed.DEMO_PERSONAS`).

Shared by the live intake test (P2-3, identity card), the end to end queue test
(P2-5), and the portal live tests. Which line to say next is picked from the calls
row (the next intake field not yet saved), so the script follows the Flow instead of
guessing the LLM's wording.

Member ID read back: once the bot has saved the caller's name, DOB, and member ID
but not yet checked them (`identity_result` still empty), it is reading them back.
The caller then says yes, or corrects what the bot got wrong, just as a person
would: the script compares what was saved with the persona's real name and ID.

P5-10 (Nov 16): member IDs are 6 digits said in two groups of three ("four one eight,
three zero two"), claim numbers 5 digits said as two and three ("seven zero, four one
two"). `claim_number` is the claim the caller names, so a run can score whether STT
heard it right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ORDER = ["full_name", "reason", "claim_number", "service_date", "claim_concern"]
LINE_FOR = {"full_name": "identity"}

_WORDS = "zero one two three four five six seven eight nine".split()


def say_digits(digits: str, *, split: int) -> str:
    """Digits one at a time, in two groups: say_digits("418302", split=3) ->
    "four one eight, three zero two"."""
    words = [_WORDS[int(c)] for c in digits]
    return f"{' '.join(words[:split])}, {' '.join(words[split:])}"


@dataclass(frozen=True)
class Persona:
    key: str
    full_name: str
    dob: str  # the real DOB (YYYY-MM-DD), as seeded
    member_id: str
    lines: dict[str, str]
    expected_identity: str = "verified"
    # The claim number the caller gives ("" when they have none).
    claim_number: str = ""


def _lines(
    *,
    name: str,
    spelled_first: str,
    dob_said: str,
    member_id: str,
    reason: str,
    claim_number: str,
    service_date: str,
    claim_concern: str,
) -> dict[str, str]:
    first, last = name.split(" ", 1)
    member_id_said = say_digits(member_id, split=3)
    return {
        "identity": (
            f"Sure. My name is {name}. My date of birth is {dob_said}. "
            f"And my member ID is {member_id_said}."
        ),
        "confirm_yes": "Yes, that's right.",
        "fix_member_id": f"No, my member ID is {member_id_said}.",
        "fix_name": f"No, my name is {first}, {spelled_first}, {last}.",
        "fix_both": (
            f"No. My name is {first}, {spelled_first}, {last}, "
            f"and my member ID is {member_id_said}."
        ),
        "reason": reason,
        "claim_number": claim_number,
        "service_date": service_date,
        "claim_concern": claim_concern,
        "readback": "Yes, that's right. Thank you.",
    }


def _claim_line(claim: str) -> str:
    return f"Yes, the claim number is {say_digits(claim, split=2)}."


PERSONAS = (
    # MRI claim 70412 denied CO-197.
    Persona(
        "maria_lopez",
        "Maria Lopez",
        "1984-03-12",
        "418302",
        _lines(
            name="Maria Lopez",
            spelled_first="M A R I A",
            dob_said="March twelfth, nineteen eighty four",
            member_id="418302",
            reason="I'm calling about a claim for my MRI. It was denied and I don't know why.",
            claim_number=_claim_line("70412"),
            service_date="It was about a month ago, at Riverside Imaging Center.",
            claim_concern="It was denied. The letter said something about no prior authorization.",
        ),
        claim_number="70412",
    ),
    # Duplicate premium autopay draft (account and payments).
    Persona(
        "james_carter",
        "James Carter",
        "1979-07-04",
        "527194",
        _lines(
            name="James Carter",
            spelled_first="J A M E S",
            dob_said="July fourth, nineteen seventy nine",
            member_id="527194",
            reason="I was charged twice for this month's premium.",
            claim_number="No, I don't have a claim number. It's my premium payment.",
            service_date="On the first of this month. Both payments went to the health plan.",
            claim_concern="The premium was taken out of my bank account twice.",
        ),
    ),
    # Cardiac stress test claim 31845 pending_info for about 3 weeks; repeat caller.
    Persona(
        "sarah_miller",
        "Sarah Miller",
        "1988-06-15",
        "639251",
        _lines(
            name="Sarah Miller",
            spelled_first="S A R A H",
            dob_said="June fifteenth, nineteen eighty eight",
            member_id="639251",
            reason="I'm checking on a claim for my cardiac stress test. It's been pending forever.",
            claim_number=_claim_line("31845"),
            service_date="About five weeks ago, at Northgate Cardiology.",
            claim_concern="It's still pending. It's been about three weeks.",
        ),
        claim_number="31845",
    ),
    # Hard name. Dermatology claim 52976 denied CO-242 (out of network).
    Persona(
        "linda_nguyen",
        "Linda Nguyen",
        "1958-04-22",
        "741526",
        _lines(
            name="Linda Nguyen",
            spelled_first="L I N D A",
            dob_said="April twenty second, nineteen fifty eight",
            member_id="741526",
            reason="My dermatology visit was denied and I want to know how to appeal.",
            claim_number=_claim_line("52976"),
            service_date="About six weeks ago, at Bayview Dermatology.",
            claim_concern="It was denied. They said the doctor was out of network.",
        ),
        claim_number="52976",
    ),
    # Knee surgery prior auth PA-500101. Gives a wrong DOB (May 7, not May 17) on
    # purpose, so identity returns not_verified and the call is flagged (docs/db.md).
    Persona(
        "ben_cole",
        "Ben Cole",
        "1990-05-17",
        "852617",
        _lines(
            name="Ben Cole",
            spelled_first="B E N",
            dob_said="May seventh, nineteen ninety",
            member_id="852617",
            reason="I want to check the status of the prior authorization for my knee surgery.",
            claim_number="I don't have a claim number. It's a prior authorization.",
            service_date="The surgery is planned for next month at Pine Hill Orthopedics.",
            claim_concern="It's still pending and I need to schedule the surgery.",
        ),
        expected_identity="not_verified",
    ),
    # Urgent care claim 46183 in review. A second Mark Hill exists (1986-02-09):
    # the DOB picks this one.
    Persona(
        "mark_hill",
        "Mark Hill",
        "1975-10-06",
        "285916",
        _lines(
            name="Mark Hill",
            spelled_first="M A R K",
            dob_said="October sixth, nineteen seventy five",
            member_id="285916",
            reason="I'm calling to check on a claim for an urgent care visit.",
            claim_number=_claim_line("46183"),
            service_date="About two weeks ago, at Cedar Urgent Care.",
            claim_concern="It still says in review and I want to know when it will be paid.",
        ),
        claim_number="46183",
    ),
    # Hard name. Asks what an explanation of benefits is (general question).
    Persona(
        "aisha_patel",
        "Aisha Patel",
        "1995-08-09",
        "174839",
        _lines(
            name="Aisha Patel",
            spelled_first="A I S H A",
            dob_said="August ninth, nineteen ninety five",
            member_id="174839",
            reason="I got an explanation of benefits in the mail and I don't understand it.",
            claim_number=_claim_line("19374"),
            service_date="It was a specialist visit last month at Lakeside Family Medicine.",
            claim_concern="I don't know if it's a bill or what I owe.",
        ),
        claim_number="19374",
    ),
)

_BY_KEY = {p.key: p for p in PERSONAS}
MARIA_LOPEZ = _BY_KEY["maria_lopez"].lines


def persona(key: str) -> Persona:
    return _BY_KEY[key]


def _name_ok(heard: str, real: str) -> bool:
    toks = re.sub(r"[^a-z\s]", " ", (heard or "").lower()).split()
    first, last = real.lower().split(" ", 1)
    return len(toks) >= 2 and toks[0] == first and toks[-1] == last


def _digits(text: str) -> str:
    from agent_assist_bot.calls import spoken_digits

    return spoken_digits(text or "")


def _id_ok(heard: str, real: str) -> bool:
    return _digits(heard) == real


def claim_heard_right(heard: str, real: str) -> bool:
    """True when the saved claim number answer holds the real claim number."""
    return bool(real) and real in _digits(heard)


def next_line(
    intake: dict[str, Any] | None,
    identity_result: str | None,
    who: Persona = PERSONAS[0],
) -> str:
    """Key of the script line that answers the bot's last turn.

    `intake` and `identity_result` come from the calls row. Fields saved but not yet
    checked mean the bot is reading the identity details back.
    """
    intake = intake or {}
    if intake.get("full_name") and not identity_result:
        name_ok = _name_ok(intake["full_name"], who.full_name)
        id_ok = _id_ok(intake.get("member_id", ""), who.member_id)
        if name_ok and id_ok:
            return "confirm_yes"
        if not name_ok and not id_ok:
            return "fix_both"
        return "fix_member_id" if name_ok else "fix_name"
    for key in ORDER:
        if not intake.get(key):
            return LINE_FOR.get(key, key)
    return "readback"
