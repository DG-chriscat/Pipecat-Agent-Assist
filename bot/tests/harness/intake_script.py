"""Scripted caller for demo persona 1, Maria Alvarez (docs/db.md).

Shared by the live intake test (P2-3) and the end to end queue test (P2-5). Which
line to say next is picked from the saved intake fields (the next field not yet
saved), so the script follows the Flow instead of guessing the LLM's wording.
"""

from __future__ import annotations

from typing import Any

# MRI claim CLM-900001 denied CO-197.
MARIA_ALVAREZ = {
    "identity": (
        "Sure. My name is Maria Alvarez. My date of birth is March twelfth, nineteen "
        "eighty four. And my member ID is E V M, one zero zero, two three one."
    ),
    "reason": "I'm calling about a claim for my MRI. It was denied and I don't know why.",
    "claim_number": "Yes, the claim number is C L M, nine zero zero, zero zero one.",
    "service_date": "It was about a month ago, at Riverside Imaging Center.",
    "claim_concern": "It was denied. The letter said something about no prior authorization.",
    "readback": "Yes, that's right. Thank you.",
}
ORDER = ["full_name", "reason", "claim_number", "service_date", "claim_concern"]
LINE_FOR = {"full_name": "identity"}


def next_line(intake: dict[str, Any] | None) -> str:
    """Key of the script line that answers the next unsaved intake field."""
    intake = intake or {}
    for key in ORDER:
        if not intake.get(key):
            return LINE_FOR.get(key, key)
    return "readback"
