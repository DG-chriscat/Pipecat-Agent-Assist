"""Scripted callers for the 5 demo personas (docs/db.md, `seed.DEMO_PERSONAS`).

Shared by the live intake test (P2-3, identity card), the end to end queue test
(P2-5), and the portal live tests. Which line to say next is picked from the calls
row (the next intake field not yet saved), so the script follows the Flow instead of
guessing the LLM's wording.

Member ID read back: once the bot has saved the caller's name, DOB, and member ID
but not yet checked them (`identity_result` still empty), it is reading them back.
The caller then says yes, or corrects what the bot got wrong, just as a person
would: the script compares what was saved with the persona's real name and ID.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ORDER = ["full_name", "reason", "claim_number", "service_date", "claim_concern"]
LINE_FOR = {"full_name": "identity"}


@dataclass(frozen=True)
class Persona:
    key: str
    full_name: str
    dob: str  # the real DOB (YYYY-MM-DD), as seeded
    member_id: str
    lines: dict[str, str]
    expected_identity: str = "verified"


def _lines(
    *,
    name: str,
    spelled_first: str,
    dob_said: str,
    member_id_said: str,
    reason: str,
    claim_number: str,
    service_date: str,
    claim_concern: str,
) -> dict[str, str]:
    first, last = name.split(" ", 1)
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


PERSONAS = (
    # MRI claim CLM-900001 denied CO-197.
    Persona(
        "maria_alvarez",
        "Maria Alvarez",
        "1984-03-12",
        "EVM100231",
        _lines(
            name="Maria Alvarez",
            spelled_first="M A R I A",
            dob_said="March twelfth, nineteen eighty four",
            member_id_said="E V M, one zero zero, two three one",
            reason="I'm calling about a claim for my MRI. It was denied and I don't know why.",
            claim_number="Yes, the claim number is C L M, nine zero zero, zero zero one.",
            service_date="It was about a month ago, at Riverside Imaging Center.",
            claim_concern="It was denied. The letter said something about no prior authorization.",
        ),
    ),
    # Duplicate premium autopay draft (account and payments).
    Persona(
        "james_chen",
        "James Chen",
        "1979-07-04",
        "EVM100232",
        _lines(
            name="James Chen",
            spelled_first="J A M E S",
            dob_said="July fourth, nineteen seventy nine",
            member_id_said="E V M, one zero zero, two three two",
            reason="I was charged twice for this month's premium.",
            claim_number="No, I don't have a claim number. It's my premium payment.",
            service_date="On the first of this month. Both payments went to the health plan.",
            claim_concern="The premium was taken out of my bank account twice.",
        ),
    ),
    # Cardiac stress test claim CLM-900003 pending_info for about 3 weeks.
    Persona(
        "aisha_patel",
        "Aisha Patel",
        "1992-11-28",
        "EVM100233",
        _lines(
            name="Aisha Patel",
            spelled_first="A I S H A",
            dob_said="November twenty eighth, nineteen ninety two",
            member_id_said="E V M, one zero zero, two three three",
            reason="I'm checking on a claim for my cardiac stress test. It's been pending forever.",
            claim_number="Yes, it's C L M, nine zero zero, zero zero three.",
            service_date="About five weeks ago, at the cardiology clinic.",
            claim_concern="It's still pending. It's been about three weeks.",
        ),
    ),
    # Dermatology claim CLM-900005 denied CO-242 (out of network).
    Persona(
        "linda_nguyen",
        "Linda Nguyen",
        "1958-01-30",
        "EVM100234",
        _lines(
            name="Linda Nguyen",
            spelled_first="L I N D A",
            dob_said="January thirtieth, nineteen fifty eight",
            member_id_said="E V M, one zero zero, two three four",
            reason="My dermatology visit was denied and I want to know how to appeal.",
            claim_number="Yes, it's C L M, nine zero zero, zero zero five.",
            service_date="About six weeks ago, at a dermatology office downtown.",
            claim_concern="It was denied. They said the doctor was out of network.",
        ),
    ),
    # Knee surgery prior auth PA-500101. Gives a wrong DOB (May 7, not May 17) on
    # purpose, so identity returns not_verified and the call is flagged (docs/db.md).
    Persona(
        "daniel_okafor",
        "Daniel Okafor",
        "1990-05-17",
        "EVM100235",
        _lines(
            name="Daniel Okafor",
            spelled_first="D A N I E L",
            dob_said="May seventh, nineteen ninety",
            member_id_said="E V M, one zero zero, two three five",
            reason="I want to check the status of the prior authorization for my knee surgery.",
            claim_number="I don't have a claim number. It's a prior authorization.",
            service_date="The surgery is planned for next month at Harbor Orthopedics.",
            claim_concern="It's still pending and I need to schedule the surgery.",
        ),
        expected_identity="not_verified",
    ),
)

_BY_KEY = {p.key: p for p in PERSONAS}
MARIA_ALVAREZ = _BY_KEY["maria_alvarez"].lines


def persona(key: str) -> Persona:
    return _BY_KEY[key]


def _name_ok(heard: str, real: str) -> bool:
    toks = re.sub(r"[^a-z\s]", " ", (heard or "").lower()).split()
    first, last = real.lower().split(" ", 1)
    return len(toks) >= 2 and toks[0] == first and toks[-1] == last


def _id_ok(heard: str, real: str) -> bool:
    return re.sub(r"[^A-Za-z0-9]", "", heard or "").upper() == real


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
