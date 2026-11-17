"""The v1 connectors over the mock DB (DC-1, DC-3, DC-8). Schema: db/migrations/0001_init.sql.

Every connector here becomes two LLM tools, `<name>_search` and `<name>_get`.
docs/connectors.md lists them with their fields; a test keeps the doc in sync.
"""

from __future__ import annotations

import re
from typing import Any

from agent_assist_bot.connectors.base import Connector, Filter


def _date(desc: str) -> dict[str, Any]:
    return {"type": "string", "format": "date", "description": f"{desc} (YYYY-MM-DD)."}


def _enum(values: list[str], desc: str) -> dict[str, Any]:
    return {"type": "string", "enum": values, "description": desc}


def _text(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def _contains(v: str) -> str:
    """ILIKE pattern for a substring match; escapes LIKE wildcards."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", v) + "%"


MEMBERS = Connector(
    name="members",
    source="Member system",
    description="member record (the current caller) with plan",
    table="members m JOIN plans p ON p.plan_id = m.plan_id",
    id_field="member_id",
    id_column="m.member_id",
    id_pattern="418302",
    member_column="m.member_id",
    columns={
        "member_id": "m.member_id",
        "name": "m.first_name || ' ' || m.last_name",
        "dob": "m.dob",
        "plan_id": "m.plan_id",
        "plan_name": "p.name",
        "plan_type": "p.plan_type",
        "coverage_start": "m.coverage_start",
        "coverage_end": "m.coverage_end",
    },
    order_by="m.member_id",
)

PLAN_TYPES = ["HMO", "PPO", "EPO", "HDHP", "Medicare Advantage", "Medicaid"]

PLANS = Connector(
    name="plans",
    source="Benefits system",
    description="health plan (benefits, copays, deductible, premium)",
    table="plans",
    id_field="plan_id",
    id_column="plan_id",
    id_pattern="PLN-HMO-SILVER",
    member_scoped=False,
    columns={
        "plan_id": "plan_id",
        "name": "name",
        "plan_type": "plan_type",
        "requires_referral": "requires_referral",
        "deductible": "deductible",
        "out_of_pocket_max": "out_of_pocket_max",
        "pcp_copay": "pcp_copay",
        "specialist_copay": "specialist_copay",
        "monthly_premium": "monthly_premium",
    },
    order_by="plan_id",
    filters=(Filter("plan_type", _enum(PLAN_TYPES, "Plan type."), "plan_type = %(plan_type)s"),),
)

CLAIM_STATUSES = ["received", "in_review", "pending_info", "approved", "paid", "denied"]

CLAIMS = Connector(
    name="claims",
    source="Claims system",
    description="claim for the current caller",
    table="claims",
    id_field="claim_id",
    id_column="claim_id",
    id_pattern="70412",
    columns={
        "claim_id": "claim_id",
        "service_date": "service_date",
        "provider": "provider_name",
        "service": "service_description",
        "billed_amount": "billed_amount",
        "status": "status",
        "denial_code": "denial_code",
        "denial_reason": "denial_reason",
        "received_on": "received_on",
        "processed_on": "processed_on",
        "allowed_amount": "allowed_amount",
        "member_responsibility": "member_responsibility",
        "auth_id": "auth_id",
    },
    search_columns=(
        "claim_id",
        "service_date",
        "provider",
        "service",
        "billed_amount",
        "status",
        "denial_code",
        "denial_reason",
        "received_on",
        "processed_on",
    ),
    order_by="service_date DESC, claim_id DESC",
    filters=(
        Filter("status", _enum(CLAIM_STATUSES, "Claim status."), "status = %(status)s"),
        Filter(
            "denial_code",
            _text("Denial code, for example CO-197."),
            "upper(denial_code) = upper(%(denial_code)s)",
        ),
        Filter(
            "service_from",
            _date("Service on or after this date"),
            "service_date >= %(service_from)s::date",
        ),
        Filter(
            "service_to",
            _date("Service on or before this date"),
            "service_date <= %(service_to)s::date",
        ),
        Filter(
            "provider",
            _text("Part of the provider name."),
            "provider_name ILIKE %(provider)s",
            loggable=False,
            transform=_contains,
        ),
    ),
)

PAYMENTS = Connector(
    name="payments",
    source="Billing system",
    description="payment (premium, copay, claim balance, refund) for the current caller",
    table="payments",
    id_field="payment_id",
    id_column="payment_id",
    id_pattern="PAY-000067",
    columns={
        "payment_id": "payment_id",
        "paid_at": "paid_at",
        "amount": "amount",
        "kind": "kind",
        "method": "method",
        "status": "status",
        "reference": "reference",
    },
    order_by="paid_at DESC, payment_id DESC",
    filters=(
        Filter(
            "kind",
            _enum(["premium", "copay", "claim_balance", "refund"], "Payment kind."),
            "kind = %(kind)s",
        ),
        Filter(
            "status", _enum(["posted", "pending", "reversed"], "Status."), "status = %(status)s"
        ),
        Filter(
            "method",
            _enum(["card", "bank", "check", "autopay"], "Payment method."),
            "method = %(method)s",
        ),
        Filter(
            "paid_from",
            _date("Paid on or after this date"),
            "paid_at >= %(paid_from)s::date",
        ),
        Filter(
            "paid_to",
            _date("Paid on or before this date"),
            "paid_at < %(paid_to)s::date + 1",
        ),
    ),
)

AUTH_STATUSES = ["pending", "approved", "denied", "expired"]

REFERRALS = Connector(
    name="referrals",
    source="Referral system",
    description="specialist referral for the current caller",
    table="referrals",
    id_field="referral_id",
    id_column="referral_id",
    id_pattern="REF-700002",
    columns={
        "referral_id": "referral_id",
        "from_provider": "from_provider",
        "to_provider": "to_provider",
        "specialty": "specialty",
        "status": "status",
        "created_on": "created_on",
        "valid_to": "valid_to",
        "visits_allowed": "visits_allowed",
    },
    order_by="created_on DESC, referral_id DESC",
    filters=(
        Filter("status", _enum(AUTH_STATUSES, "Referral status."), "status = %(status)s"),
        Filter(
            "specialty",
            _text("Specialty, for example Cardiology."),
            "specialty ILIKE %(specialty)s",
            transform=_contains,
        ),
    ),
)

PRIOR_AUTHORIZATIONS = Connector(
    name="prior_authorizations",
    source="Utilization management",
    description="prior authorization for the current caller",
    table="prior_authorizations",
    id_field="auth_id",
    id_column="auth_id",
    id_pattern="PA-500101",
    columns={
        "auth_id": "auth_id",
        "service": "service_description",
        "provider": "provider_name",
        "status": "status",
        "requested_on": "requested_on",
        "decided_on": "decided_on",
        "valid_from": "valid_from",
        "valid_to": "valid_to",
        "notes": "notes",
    },
    order_by="requested_on DESC, auth_id DESC",
    filters=(
        Filter("status", _enum(AUTH_STATUSES, "Authorization status."), "status = %(status)s"),
    ),
)

INTERACTIONS = Connector(
    name="interactions",
    source="Interaction history",
    description="past call (date, duration, queue, outcome, notes) by the current caller",
    table="interactions",
    id_field="interaction_id",
    id_column="interaction_id",
    id_pattern="INT-000001",
    columns={
        "interaction_id": "interaction_id",
        "started_at": "started_at",
        "duration_seconds": "duration_seconds",
        "queue": "queue",
        "outcome": "outcome",
        "notes": "notes",
    },
    order_by="started_at DESC, interaction_id DESC",
    filters=(
        Filter("queue", _enum(["claims", "billing", "general"], "Queue."), "queue = %(queue)s"),
        Filter("since", _date("Calls on or after this date"), "started_at >= %(since)s::date"),
    ),
)

# Words that never help a keyword match.
_STOP = frozenset(
    "a an and are about at be by can did do does for from has have how i in is it me my "
    "of on or the this to was what when where who why with you your".split()
)
_MAX_TERMS = 8


def kb_terms(query: str) -> list[str]:
    """Keyword terms from free text: lower case, deduped, stop words dropped."""
    out: list[str] = []
    for t in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", (query or "").lower()):
        if len(t) > 1 and t not in _STOP and t not in out:
            out.append(t)
    return out[:_MAX_TERMS]


def _kb_keyword(_c: Connector, args: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Score = 3 per term in the title + 2 per term in tags + 1 per term in the body."""
    terms = kb_terms(args.get("query", ""))
    if not terms:
        return "", "", {}
    parts, params = [], {}
    for i, t in enumerate(terms):
        p = f"_kw{i}"
        params[p] = _contains(t)
        parts.append(
            f"(CASE WHEN title ILIKE %({p})s THEN 3 ELSE 0 END"
            f" + CASE WHEN array_to_string(tags, ' ') ILIKE %({p})s THEN 2 ELSE 0 END"
            f" + CASE WHEN body ILIKE %({p})s THEN 1 ELSE 0 END)"
        )
    score = " + ".join(parts)
    return f"({score}) > 0", f"({score}) DESC", params


KB_ARTICLES = Connector(
    name="kb_articles",
    source="Knowledge base",
    description="knowledge base article (billing and claims procedures)",
    table="kb_articles",
    id_field="article_id",
    id_column="article_id",
    id_pattern="KB-CL-002",
    member_scoped=False,
    columns={
        "article_id": "article_id",
        "title": "title",
        "category": "category",
        "tags": "tags",
        "summary": "left(body, 160)",
        "body": "body",
        "updated_on": "updated_on",
    },
    search_columns=("article_id", "title", "category", "tags", "summary"),
    order_by="article_id",
    filters=(
        Filter(
            "query",
            _text("Keywords to match against title, tags, and body, e.g. 'CO-197 denial'."),
            "",  # scored by _kb_keyword
            loggable=False,
        ),
        Filter("category", _enum(["billing", "claims"], "Category."), "category = %(category)s"),
    ),
    keyword_search=_kb_keyword,
)

CONNECTORS: tuple[Connector, ...] = (
    MEMBERS,
    PLANS,
    CLAIMS,
    PAYMENTS,
    REFERRALS,
    PRIOR_AUTHORIZATIONS,
    INTERACTIONS,
    KB_ARTICLES,
)
