"""Demo personas for the portal's Demos page (P5-10, docs/demos.md).

    GET /demo/personas -> {phone_line: {number, password_hint},
                           features: [{id, label}],
                           personas: [persona]}

Behind the shared login like every route. The personas live in demo_personas.yaml
next to this file; server/tests/test_demo_personas.py checks them against the seed.
Each persona gets `plan` (the plan name from the seed) and `member_id_groups` ("418 302",
how the ID is read) added here, so the YAML cannot drift from the seed on those.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

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

router = APIRouter()


def _groups(member_id: str) -> str:
    return f"{member_id[:3]} {member_id[3:]}"


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    return yaml.safe_load(PERSONAS_FILE.read_text())


def load() -> list[dict[str, Any]]:
    plans = {p.plan_id: p.name for p in seed.PLANS}
    out = []
    for p in _raw()["personas"]:
        out.append(
            {
                **p,
                "plan": plans.get(p.get("plan_id") or "", "Not a member"),
                "member_id_groups": _groups(str(p["member_id"])),
                "hero": bool(p.get("hero", False)),
                "kb_articles": list(p.get("kb_articles") or []),
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
        "personas": load(),
    }
