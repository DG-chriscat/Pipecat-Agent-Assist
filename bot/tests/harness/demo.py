"""P5-4 demo readiness: score scripted demo runs against the PRD success measures.

Each run is one dict (one JSON line from server/tests/test_demo_runs_live.py). The
targets are the PRD "Proposed success measures for the demo" table. "Cards rated
useful" is left blank: Chris's demo agents fill it in by hand.
"""

from __future__ import annotations

import statistics
from typing import Any

TARGETS = {
    "full_loop": (9, 10),  # scripted calls that finish the full loop
    "identity": (10, 10),  # identity checks correct
    "intake_secs": 120.0,  # typical intake time, under 2 minutes
    "accept_secs": 2.0,  # Accept to hearing the caller
    "card_secs": 3.0,  # trigger words to card on screen
}

HEADER = (
    "| Run | Persona | Pack | Identity (want / got) | Intake (s) | Accept to caller (s) "
    "| Trigger to card (s) | Card | Wrap up saved | Full loop | Cards useful |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


def _under(value: float | None, limit: float) -> bool:
    return value is not None and value < limit


def run_checks(r: dict[str, Any]) -> dict[str, bool]:
    return {
        "full_loop": bool(r.get("full_loop")),
        "intake": _under(r.get("intake_secs"), TARGETS["intake_secs"]),
        "accept": _under(r.get("accept_to_caller_audio_secs"), TARGETS["accept_secs"]),
        "card": _under(r.get("trigger_word_to_card_secs"), TARGETS["card_secs"]),
        "identity": r.get("identity_result") in (r.get("identity_expected") or []),
    }


def _cell(value: float | None, ok: bool) -> str:
    if value is None:
        return "- fail"
    return f"{value:.2f} {'pass' if ok else 'fail'}"


def table_row(r: dict[str, Any]) -> str:
    c = run_checks(r)
    want = " or ".join(r.get("identity_expected") or [])
    got = r.get("identity_result") or "-"
    cells = [
        str(r["run"]),
        r.get("persona", ""),
        f"`{r.get('pack', '')}`",
        f"{want} / {got} {'pass' if c['identity'] else 'fail'}",
        _cell(r.get("intake_secs"), c["intake"]),
        _cell(r.get("accept_to_caller_audio_secs"), c["accept"]),
        _cell(r.get("trigger_word_to_card_secs"), c["card"]),
        f"`{r['card']}`" if r.get("card") else "-",
        "yes" if r.get("wrap_up_saved") else "no",
        "pass" if c["full_loop"] else "fail",
        "",
    ]
    return "| " + " | ".join(cells) + " |"


def _values(runs: list[dict[str, Any]], key: str) -> list[float]:
    return [r[key] for r in runs if r.get(key) is not None]


def scorecard(runs: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [run_checks(r) for r in runs]
    n = len(runs)
    loop = sum(c["full_loop"] for c in checks)
    ident = sum(c["identity"] for c in checks)
    need_loop = TARGETS["full_loop"][0] * n / TARGETS["full_loop"][1]
    intake = _values(runs, "intake_secs")
    accept = _values(runs, "accept_to_caller_audio_secs")
    card = _values(runs, "trigger_word_to_card_secs")
    return {
        "full_loop": (loop, n, loop >= need_loop),
        "identity": (ident, n, ident == n),
        "intake_median_secs": statistics.median(intake) if intake else None,
        "intake_max_secs": max(intake) if intake else None,
        "accept_median_secs": statistics.median(accept) if accept else None,
        "accept_max_secs": max(accept) if accept else None,
        "card_median_secs": statistics.median(card) if card else None,
        "card_max_secs": max(card) if card else None,
    }
