"""Demo personas for the portal's Demos page (P5-10, docs/demos.md).

    GET /demo/personas -> {phone_line: {number, password_hint},
                           features: [{id, label}],
                           expect_kinds: [{id, label}],
                           personas: [persona]}
    GET /demo/personas/{key}/caller -> the caller page's view (P5-11): who you are and
                           the beats, caller side only (no expect, no agent notes)

Behind the shared login like every route. The personas live in demo_personas.yaml
next to this file; server/tests/test_demo_personas.py checks them against the seed.
Each persona gets `plan` (the plan name from the seed) and `member_id_groups` (\"418 302\",
how the ID is read) added here, so the YAML cannot drift from the seed on those.

Talk tracks (P5-11, docs/demos.md): each persona's `talk_track` is the caller's side of
the call as beats, in call order (intake to the bot, the conversation with the agent,
closing). A beat has `say`, up to three `if_asked` backup lines, an optional caller
`note`, an `agent_note` for the tester (what the agent should do), and `expect`: what
should show on the agent's screen after the beat. Optional feature beats carry
`optional: true` and a `feature`. server/tests/test_talk_tracks.py checks every expect
item against the packs and the seed. The old `script` field is derived from the track
(the non optional beats), so the caller page and older clients keep working.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

from agent_assist_server.db import seed

PERSONAS_FILE = Path(__file__).with_name("demo_personas.yaml")

# Feature chips on the Demos page, in display order.
FEATURES: dict[str, str] = {
    "identity_verified": "Identity verified",
    "close_match": "Close match verification",
    "not_verified": "Verify caller",
    "name_collision": "Same name, DOB decides",
    "not_found": "Not found, record locked",
    "repeat_caller": "Repeat caller",
    "duplicate_charge": "Duplicate charge alert",
    "kb_article": "KB article",
    "tone_tip": "Tone tip",
    "wrap_up": "Wrap up",
    "emergency": "Emergency guardrail",
}

# What an expect item points at on the agent's screen (P5-11), in display order.
EXPECT_KINDS: dict[str, str] = {
    "emergency": "Emergency line",
    "queue": "Incoming call",
    "identity": "Identity",
    "verify": "Verify caller",
    "locked": "Locked records",
    "repeat_caller": "Repeat caller",
    "hero": "Next best step",
    "card": "Card",
    "kb": "Article",
    "plan": "Call plan",
    "tone_tip": "Tone tip",
    "ask": "Ask bar",
    "wrap_up": "Wrap up",
}
# Expect kinds that must name what they point at.
_NEEDS_ID = frozenset({"hero", "card", "kb", "plan", "ask"})
PHASES = ("intake", "agent", "closing")
# Optional feature beats (P5-11).
OPTIONAL_FEATURES: dict[str, str] = {
    "tone_tip": "Sound frustrated",
    "ask_bar": "Ask bar follow up",
    "verify": "Verification moment",
}
MAX_IF_ASKED = 3

router = APIRouter()


def _groups(member_id: str) -> str:
    return f"{member_id[:3]} {member_id[3:]}"


def validate_track(key: str, track: Any) -> None:
    """Raise ValueError when a talk track is not well formed (shape only; the
    packs and seed checks live in server/tests/test_talk_tracks.py)."""

    def bad(msg: str) -> ValueError:
        return ValueError(f"{key}: talk_track {msg}")

    if not isinstance(track, list) or not track:
        raise bad("must be a non empty list of beats")
    last = 0
    for i, b in enumerate(track):
        where = f"beat {i + 1}"
        if not isinstance(b, dict):
            raise bad(f"{where} must be a mapping")
        phase = b.get("phase")
        if phase not in PHASES:
            raise bad(f"{where} phase must be one of {PHASES}")
        if i == 0 and phase != "intake":
            raise bad("must start with an intake beat")
        n = PHASES.index(phase)
        if n < last:
            raise bad(f"{where} is out of call order")
        last = n
        if b.get("to") != ("bot" if phase == "intake" else "agent"):
            raise bad(f"{where}: intake beats are to the bot, the rest to the agent")
        if not str(b.get("say") or "").strip():
            raise bad(f"{where} needs a say line")
        if_asked = b.get("if_asked", [])
        if not isinstance(if_asked, list) or len(if_asked) > MAX_IF_ASKED:
            raise bad(f"{where} if_asked takes up to {MAX_IF_ASKED} lines")
        if any(not str(x).strip() for x in if_asked):
            raise bad(f"{where} has an empty if_asked line")
        feature = b.get("feature")
        if feature is not None and (feature not in OPTIONAL_FEATURES or not b.get("optional")):
            raise bad(
                f"{where}: a feature beat must be optional and one of {list(OPTIONAL_FEATURES)}"
            )
        if b.get("optional") and phase != "agent":
            raise bad(f"{where}: only agent beats can be optional")
        for e in b.get("expect", []):
            if not isinstance(e, dict) or e.get("kind") not in EXPECT_KINDS:
                raise bad(f"{where} has an expect item of unknown kind")
            if not str(e.get("text") or "").strip():
                raise bad(f"{where} has an expect item with no text")
            if e["kind"] in _NEEDS_ID and not e.get("id"):
                raise bad(f"{where}: a {e['kind']} expect item needs an id")
            if e["kind"] == "ask" and not e.get("ask"):
                raise bad(f"{where}: an ask expect item needs the question to type")
            if e["kind"] == "wrap_up" and not e.get("reason_code"):
                raise bad(f"{where}: a wrap_up expect item needs a reason_code")


def _beat(b: dict[str, Any]) -> dict[str, Any]:
    return {
        **b,
        "if_asked": list(b.get("if_asked") or []),
        "expect": list(b.get("expect") or []),
        "optional": bool(b.get("optional", False)),
    }


def _script(track: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The P5-10 `script`: the non optional beats' lines, with the caller notes."""
    return [
        {"to": b["to"], "say": b["say"], **({"note": b["note"]} if b.get("note") else {})}
        for b in track
        if not b["optional"]
    ]


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    return yaml.safe_load(PERSONAS_FILE.read_text())


def load() -> list[dict[str, Any]]:
    plans = {p.plan_id: p.name for p in seed.PLANS}
    out = []
    for p in _raw()["personas"]:
        validate_track(p["key"], p.get("talk_track"))
        track = [_beat(b) for b in p["talk_track"]]
        out.append(
            {
                **p,
                "plan": plans.get(p.get("plan_id") or "", "Not a member"),
                "member_id_groups": _groups(str(p["member_id"])),
                "hero": bool(p.get("hero", False)),
                "kb_articles": list(p.get("kb_articles") or []),
                "talk_track": track,
                "script": _script(track),
            }
        )
    return out


def phone_line() -> dict[str, str]:
    return dict(_raw()["phone_line"])


@router.get("/demo/personas")
def get_personas() -> dict[str, Any]:
    return {
        "phone_line": phone_line(),
        "features": [{"id": k, "label": v} for k, v in FEATURES.items()],
        "expect_kinds": [{"id": k, "label": v} for k, v in EXPECT_KINDS.items()],
        "personas": load(),
    }


# What the caller page may show of a beat (P5-11): the caller's side only.
CALLER_BEAT_KEYS = ("phase", "to", "step", "say", "if_asked", "note", "optional", "feature")


def caller_view(p: dict[str, Any]) -> dict[str, Any]:
    """The persona as the caller page shows it: who you are and what to say. No
    agent screen (expect), no agent notes, no audience notes."""
    return {
        "key": p["key"],
        "name": p["name"],
        "dob_said": p["dob_said"],
        "member_id_groups": p["member_id_groups"],
        "case": p["case"],
        "warning": p.get("warning"),
        "talk_track": [{k: b[k] for k in CALLER_BEAT_KEYS if k in b} for b in p["talk_track"]],
    }


@router.get("/demo/personas/{key}/caller")
def get_caller_view(key: str) -> dict[str, Any]:
    p = next((p for p in load() if p["key"] == key), None)
    if p is None:
        raise HTTPException(status_code=404, detail="no such demo persona")
    return caller_view(p)
