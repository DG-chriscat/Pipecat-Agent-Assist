"""Deterministic fake seed data for the mock database (DC-3, DC-8).

Everything here is invented. Names are generic, phone numbers use the
fictional 555-01xx range, IDs follow no real payer's format. No real patient
data, ever.

Dates are relative to an `as_of` day (default: today, UTC) so "called in the
last 30 days" stays true whenever the demo is reseeded. For a fixed `as_of`
the output is identical on every run.
"""

from __future__ import annotations

import datetime as dt
import random
import re
from collections.abc import Callable
from dataclasses import astuple, dataclass, fields

import psycopg

RNG_SEED = 20261115


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    plan_type: str
    requires_referral: bool
    deductible: float
    out_of_pocket_max: float
    pcp_copay: float
    specialist_copay: float
    monthly_premium: float


@dataclass(frozen=True)
class Member:
    member_id: str
    first_name: str
    last_name: str
    dob: dt.date
    phone: str
    plan_id: str
    coverage_start: dt.date
    coverage_end: dt.date | None = None


@dataclass(frozen=True)
class PriorAuth:
    auth_id: str
    member_id: str
    service_description: str
    provider_name: str
    status: str
    requested_on: dt.date
    decided_on: dt.date | None
    valid_from: dt.date | None
    valid_to: dt.date | None
    notes: str | None


@dataclass(frozen=True)
class Claim:
    claim_id: str
    member_id: str
    service_date: dt.date
    provider_name: str
    service_description: str
    billed_amount: float
    allowed_amount: float | None
    member_responsibility: float | None
    status: str
    denial_code: str | None
    denial_reason: str | None
    received_on: dt.date
    processed_on: dt.date | None
    auth_id: str | None = None


@dataclass(frozen=True)
class Payment:
    payment_id: str
    member_id: str
    paid_at: dt.datetime
    amount: float
    kind: str
    method: str
    status: str
    reference: str


@dataclass(frozen=True)
class Referral:
    referral_id: str
    member_id: str
    from_provider: str
    to_provider: str
    specialty: str
    status: str
    created_on: dt.date
    valid_to: dt.date | None
    visits_allowed: int | None


@dataclass(frozen=True)
class Interaction:
    interaction_id: str
    member_id: str
    started_at: dt.datetime
    duration_seconds: int
    queue: str
    outcome: str
    notes: str


@dataclass(frozen=True)
class KbArticle:
    article_id: str
    title: str
    category: str
    tags: list[str]
    body: str
    updated_on: dt.date

    def __hash__(self) -> int:  # tags is a list; hash by id
        return hash(self.article_id)


@dataclass(frozen=True)
class Persona:
    """A scripted demo caller. `reason` is why they call; see docs/db.md."""

    member_id: str
    first_name: str
    last_name: str
    dob: dt.date
    plan_id: str
    pack: str
    reason: str


@dataclass(frozen=True)
class SeedData:
    plans: list[Plan]
    members: list[Member]
    prior_authorizations: list[PriorAuth]
    claims: list[Claim]
    payments: list[Payment]
    referrals: list[Referral]
    interactions: list[Interaction]
    kb_articles: list[KbArticle]


PLANS = [
    Plan("PLN-HMO-SILVER", "Evergreen HMO Silver", "HMO", True, 1500, 6000, 25, 50, 412.00),
    Plan("PLN-HMO-GOLD", "Evergreen HMO Gold", "HMO", True, 500, 4000, 15, 35, 528.00),
    Plan("PLN-PPO-GOLD", "Summit PPO Gold", "PPO", False, 750, 5000, 20, 45, 610.00),
    Plan("PLN-EPO-BRONZE", "Harbor EPO Bronze", "EPO", False, 3500, 8700, 40, 80, 298.00),
    Plan("PLN-HDHP-HSA", "Summit HDHP with HSA", "HDHP", False, 3200, 7000, 0, 0, 265.00),
    Plan(
        "PLN-MA-PLUS",
        "Evergreen Medicare Advantage Plus",
        "Medicare Advantage",
        True,
        0,
        3900,
        0,
        30,
        0.00,
    ),
]

# P5-10 (Chris, Nov 16): demo friendly data so speech to text hears it right.
# Member IDs: 6 digits read as two groups of three (418 302), no leading 0, no digit
# twice in a row, and no group shared by two members. Claim numbers: 5 digits (70412).
# Names: short and common, except the two deliberately hard names for the identity
# verification demo (HARD_NAMES). Two members are named Mark Hill (different DOB), so
# the DOB check still matters.
HARD_NAMES = frozenset({"Linda Nguyen", "Aisha Patel"})

# The scripted demo callers who are seed members. Keep docs/db.md and
# server/agent_assist_server/demo_personas.yaml in sync (a test checks the YAML).
DEMO_PERSONAS = [
    Persona(
        "418302",
        "Maria",
        "Lopez",
        dt.date(1984, 3, 12),
        "PLN-HMO-SILVER",
        "claims_status",
        "MRI claim 70412 denied (CO-197, no prior authorization). Second call in a week; "
        "the next best step is a retro authorization review.",
    ),
    Persona(
        "527194",
        "James",
        "Carter",
        dt.date(1979, 7, 4),
        "PLN-PPO-GOLD",
        "account_payments",
        "Charged twice for this month's premium (duplicate autopay draft).",
    ),
    Persona(
        "639251",
        "Sarah",
        "Miller",
        dt.date(1988, 6, 15),
        "PLN-HMO-GOLD",
        "claims_status",
        "Cardiac stress test claim 31845 stuck in pending_info for 3 weeks; frustrated "
        "repeat caller. Provider has not sent the itemized bill.",
    ),
    Persona(
        "741526",
        "Linda",
        "Nguyen",
        dt.date(1958, 4, 22),
        "PLN-MA-PLUS",
        "claims_status",
        "Hard name. Dermatology claim 52976 denied CO-242 (out of network); asks how to "
        "appeal. STT often mishears the last name, so intake ends in a close match.",
    ),
    Persona(
        "174839",
        "Aisha",
        "Patel",
        dt.date(1995, 8, 9),
        "PLN-HMO-SILVER",
        "general_question",
        "Hard name. Asks what an explanation of benefits is (KB article). Often heard as "
        "Ayesha, so intake ends in a close match.",
    ),
    Persona(
        "852617",
        "Ben",
        "Cole",
        dt.date(1990, 5, 17),
        "PLN-EPO-BRONZE",
        "claims_status",
        "Knee surgery prior auth PA-500101 status. Scripted to give a wrong date of birth "
        "(May 7, not May 17), so identity comes back not_verified and the call is flagged.",
    ),
    Persona(
        "263048",
        "David",
        "Brooks",
        dt.date(1971, 9, 25),
        "PLN-HDHP-HSA",
        "general_question",
        "General benefits question: how the deductible and out of pocket maximum work "
        "(KB article).",
    ),
    Persona(
        "307495",
        "Emily",
        "Stone",
        dt.date(1983, 2, 14),
        "PLN-PPO-GOLD",
        "claims_status",
        "Emergency guardrail: describes chest pain during intake; the bot gives the 911 "
        "line and flags the call.",
    ),
    Persona(
        "285916",
        "Mark",
        "Hill",
        dt.date(1975, 10, 6),
        "PLN-HMO-GOLD",
        "claims_status",
        "Urgent care claim 46183 in review. A second Mark Hill (1986-02-09, 396127) "
        "exists, so the date of birth decides who is calling.",
    ),
]
PERSONA_IDS = frozenset(p.member_id for p in DEMO_PERSONAS)

# Same name as Mark Hill above, different DOB, plan, and ID.
MARK_HILL_TWIN = ("396127", "Mark", "Hill", dt.date(1986, 2, 9), "PLN-HDHP-HSA")

# Everyone else: short, easy names (the member ID is generated, see _filler_ids).
_FILLERS = [
    ("Anna", "Price"),
    ("Kate", "Ward"),
    ("Luke", "Fox"),
    ("Jane", "Bell"),
    ("Paul", "Grant"),
    ("Rose", "Lane"),
    ("Jack", "Moss"),
    ("Lily", "Hart"),
    ("Sam", "Shaw"),
    ("Claire", "Webb"),
    ("Nick", "Dean"),
    ("Grace", "Knox"),
    ("Adam", "Burke"),
    ("Beth", "West"),
    ("Joe", "Hale"),
]
_PROVIDERS = [
    "Lakeside Family Medicine",
    "Riverside Imaging Center",
    "Northgate Cardiology",
    "Pine Hill Orthopedics",
    "Bayview Dermatology",
    "Cedar Urgent Care",
    "Meadow Physical Therapy",
    "Summit Lab Services",
]
_SERVICES = [
    "Office visit, established patient",
    "Annual preventive exam",
    "Lipid panel",
    "Physical therapy session",
    "Urgent care visit",
    "X-ray, knee, 2 views",
    "Comprehensive metabolic panel",
    "Specialist consult",
]
_DENIALS = [
    ("CO-16", "Claim lacks information needed for adjudication"),
    ("CO-50", "Not deemed a medical necessity by the payer"),
    ("CO-18", "Exact duplicate claim or service"),
    ("CO-29", "Time limit for filing has expired"),
    ("CO-197", "Precertification or authorization absent"),
    ("CO-242", "Services not provided by network providers"),
]
_QUEUES = ["claims", "billing", "general"]
_OUTCOMES = {
    "claims": [
        "resolved",
        "explained claim status",
        "sent claim for reprocessing",
        "escalated to claims review",
    ],
    "billing": ["resolved", "updated payment method", "refund requested", "explained bill"],
    "general": ["resolved", "sent ID card", "updated address", "transferred to pharmacy"],
}


def syllables(word: str) -> int:
    """Rough syllable count (vowel groups, silent final e): the easy name check."""
    w = word.lower()
    n = len(re.findall(r"[aeiou]+", w))
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(n, 1)


def speakable(digits: str) -> bool:
    """No leading 0 and no digit twice in a row (no "one one" or "double one")."""
    return (
        digits.isdigit()
        and digits[0] != "0"
        and all(a != b for a, b in zip(digits, digits[1:], strict=False))
    )


def _filler_ids(n: int, taken: set[str]) -> list[str]:
    """`n` speakable 6 digit IDs whose groups of three are not used by any other ID."""
    rng = random.Random(RNG_SEED + 7)
    heads = {t[:3] for t in taken}
    tails = {t[3:] for t in taken}
    out: list[str] = []
    while len(out) < n:
        mid = f"{rng.randrange(100000, 1000000)}"
        if not speakable(mid) or mid[:3] in heads or mid[3:] in tails:
            continue
        heads.add(mid[:3])
        tails.add(mid[3:])
        out.append(mid)
    return out


def _phone(n: int) -> str:
    # Fictional range: 555-0100 to 555-0199.
    return f"+1312555{100 + n:04d}"


def _at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=dt.UTC)


def _months_back(day: dt.date, k: int) -> dt.date:
    """First day of the month `k` months before `day`'s month."""
    total = day.year * 12 + day.month - 1 - k
    return dt.date(total // 12, total % 12 + 1, 1)


def _members() -> list[Member]:
    rng = random.Random(RNG_SEED)
    out = [
        Member(
            p.member_id, p.first_name, p.last_name, p.dob, _phone(i), p.plan_id, dt.date(2024, 1, 1)
        )
        for i, p in enumerate(DEMO_PERSONAS)
    ]
    twin_id, first, last, dob, plan = MARK_HILL_TWIN
    out.append(Member(twin_id, first, last, dob, _phone(len(out)), plan, dt.date(2025, 1, 1)))
    plan_ids = [p.plan_id for p in PLANS]
    ids = _filler_ids(len(_FILLERS), {m.member_id for m in out})
    base = len(out)
    for i, ((first, last), mid) in enumerate(zip(_FILLERS, ids, strict=True)):
        dob = dt.date(1950, 1, 1) + dt.timedelta(days=rng.randrange(0, 365 * 52))
        out.append(
            Member(
                mid,
                first,
                last,
                dob,
                _phone(base + i),
                plan_ids[i % len(plan_ids)],
                dt.date(2023 + i % 3, 1 + i % 12, 1),
            )
        )
    return out


def _filler_claim_ids(rng: random.Random, taken: set[str], members: set[str]) -> Callable[[], str]:
    """A function giving the next unused 5 digit claim ID (no leading 0, never part
    of a member ID)."""

    def next_id() -> str:
        while True:
            cid = f"{rng.randrange(10000, 100000)}"
            if cid in taken or any(cid in m for m in members):
                continue
            taken.add(cid)
            return cid

    return next_id


MARIA, JAMES, SARAH, LINDA, AISHA, BEN, DAVID, EMILY, MARK = (p.member_id for p in DEMO_PERSONAS)
MARK_TWIN = MARK_HILL_TWIN[0]


def build_seed(as_of: dt.date | None = None) -> SeedData:
    as_of = as_of or dt.datetime.now(dt.UTC).date()
    rng = random.Random(RNG_SEED + 1)
    members = _members()
    plans_by_id = {p.plan_id: p for p in PLANS}
    fill = [m.member_id for m in members[len(DEMO_PERSONAS) + 1 :]]
    d = lambda days: as_of - dt.timedelta(days=days)  # noqa: E731

    auths: list[PriorAuth] = [
        PriorAuth(
            "PA-500101",
            BEN,
            "Arthroscopic knee surgery, right",
            "Pine Hill Orthopedics",
            "pending",
            d(9),
            None,
            None,
            None,
            "Awaiting clinical notes from provider.",
        ),
        PriorAuth(
            "PA-500102",
            SARAH,
            "Cardiac stress test",
            "Northgate Cardiology",
            "approved",
            d(60),
            d(57),
            d(57),
            d(57) + dt.timedelta(days=90),
            None,
        ),
        PriorAuth(
            "PA-500103",
            fill[0],
            "MRI, lumbar spine",
            "Riverside Imaging Center",
            "approved",
            d(40),
            d(38),
            d(38),
            d(38) + dt.timedelta(days=60),
            None,
        ),
        PriorAuth(
            "PA-500104",
            fill[4],
            "Sleep study",
            "Lakeside Family Medicine",
            "denied",
            d(75),
            d(70),
            None,
            None,
            "Criteria not met; appeal window 180 days.",
        ),
        PriorAuth(
            "PA-500105",
            fill[9],
            "Physical therapy, 12 visits",
            "Meadow Physical Therapy",
            "expired",
            d(200),
            d(196),
            d(196),
            d(106),
            None,
        ),
    ]

    claims: list[Claim] = [
        # Maria Lopez: MRI denied for missing auth.
        Claim(
            "70412",
            MARIA,
            d(34),
            "Riverside Imaging Center",
            "MRI, knee, left",
            1850.00,
            None,
            None,
            "denied",
            "CO-197",
            "Precertification or authorization absent",
            d(30),
            d(16),
        ),
        Claim(
            "70358",
            MARIA,
            d(70),
            "Lakeside Family Medicine",
            "Office visit, established patient",
            180.00,
            120.00,
            25.00,
            "paid",
            None,
            None,
            d(66),
            d(52),
        ),
        # Sarah Miller: pending info.
        Claim(
            "31845",
            SARAH,
            d(45),
            "Northgate Cardiology",
            "Cardiac stress test",
            2400.00,
            None,
            None,
            "pending_info",
            None,
            None,
            d(24),
            None,
            "PA-500102",
        ),
        Claim(
            "31962",
            SARAH,
            d(90),
            "Northgate Cardiology",
            "Specialist consult",
            320.00,
            210.00,
            35.00,
            "paid",
            None,
            None,
            d(85),
            d(71),
        ),
        # Linda Nguyen: out of network.
        Claim(
            "52976",
            LINDA,
            d(28),
            "Bayview Dermatology",
            "Skin biopsy",
            640.00,
            None,
            None,
            "denied",
            "CO-242",
            "Services not provided by network providers",
            d(25),
            d(12),
        ),
        # Ben Cole: nothing billed yet for the surgery; one old paid claim.
        Claim(
            "64281",
            BEN,
            d(50),
            "Pine Hill Orthopedics",
            "X-ray, knee, 2 views",
            260.00,
            140.00,
            40.00,
            "paid",
            None,
            None,
            d(47),
            d(33),
        ),
        # James Carter: routine.
        Claim(
            "58139",
            JAMES,
            d(20),
            "Cedar Urgent Care",
            "Urgent care visit",
            290.00,
            None,
            None,
            "in_review",
            None,
            None,
            d(17),
            None,
        ),
        # Mark Hill (1975): the claim he calls about.
        Claim(
            "46183",
            MARK,
            d(12),
            "Cedar Urgent Care",
            "Urgent care visit",
            310.00,
            None,
            None,
            "in_review",
            None,
            None,
            d(9),
            None,
        ),
        # Aisha Patel: the EOB she asks about.
        Claim(
            "19374",
            AISHA,
            d(26),
            "Lakeside Family Medicine",
            "Specialist consult",
            320.00,
            210.00,
            50.00,
            "paid",
            None,
            None,
            d(22),
            d(10),
        ),
        # David Brooks: lab work toward the deductible.
        Claim(
            "27591",
            DAVID,
            d(40),
            "Summit Lab Services",
            "Lipid panel",
            180.00,
            95.00,
            95.00,
            "paid",
            None,
            None,
            d(36),
            d(24),
        ),
        # Emily Stone: routine.
        Claim(
            "35718",
            EMILY,
            d(60),
            "Lakeside Family Medicine",
            "Annual preventive exam",
            240.00,
            160.00,
            0.00,
            "paid",
            None,
            None,
            d(56),
            d(42),
        ),
        # Guaranteed filler denials regardless of RNG draws.
        Claim(
            "90271",
            fill[6],
            d(55),
            "Summit Lab Services",
            "Comprehensive metabolic panel",
            210.00,
            None,
            None,
            "denied",
            "CO-50",
            "Not deemed a medical necessity by the payer",
            d(52),
            d(40),
        ),
        Claim(
            "90385",
            fill[12],
            d(65),
            "Cedar Urgent Care",
            "Urgent care visit",
            290.00,
            None,
            None,
            "denied",
            "CO-18",
            "Exact duplicate claim or service",
            d(61),
            d(50),
        ),
    ]
    next_claim_id = _filler_claim_ids(
        rng, {c.claim_id for c in claims}, {m.member_id for m in members}
    )
    for m in members[len(DEMO_PERSONAS) + 1 :]:
        for _ in range(rng.randint(1, 3)):
            svc_day = rng.randint(10, 160)
            billed = round(rng.uniform(90, 1400), 2)
            status = rng.choice(
                ["paid", "paid", "paid", "approved", "in_review", "received", "denied"]
            )
            code = reason = allowed = resp = processed = None
            if status == "denied":
                code, reason = rng.choice(_DENIALS)
            if status in ("paid", "approved"):
                allowed = round(billed * 0.62, 2)
                resp = round(min(allowed, plans_by_id[m.plan_id].specialist_copay or 20), 2)
            if status in ("paid", "approved", "denied"):
                processed = d(max(svc_day - 18, 1))
            claims.append(
                Claim(
                    next_claim_id(),
                    m.member_id,
                    d(svc_day),
                    rng.choice(_PROVIDERS),
                    rng.choice(_SERVICES),
                    billed,
                    allowed,
                    resp,
                    status,
                    code,
                    reason,
                    d(max(svc_day - 4, 1)),
                    processed,
                )
            )

    payments: list[Payment] = []
    p = 1
    for m in members:
        premium = plans_by_id[m.plan_id].monthly_premium
        if premium <= 0:
            continue
        for k in range(3):
            month_day = _months_back(as_of, k)
            payments.append(
                Payment(
                    f"PAY-{p:06d}",
                    m.member_id,
                    _at(month_day, 6),
                    premium,
                    "premium",
                    "autopay",
                    "posted",
                    f"AP{month_day:%Y%m}{p:04d}",
                )
            )
            p += 1
    # James Carter: duplicate autopay draft this month.
    this_month = as_of.replace(day=1)
    payments.append(
        Payment(
            f"PAY-{p:06d}",
            JAMES,
            _at(this_month, 6, 2),
            plans_by_id["PLN-PPO-GOLD"].monthly_premium,
            "premium",
            "autopay",
            "posted",
            f"AP{this_month:%Y%m}{p:04d}",
        )
    )
    p += 1
    payments.append(
        Payment(
            f"PAY-{p:06d}",
            MARIA,
            _at(d(45), 14, 20),
            25.00,
            "copay",
            "card",
            "posted",
            f"CC{p:06d}",
        )
    )
    p += 1
    payments.append(
        Payment(
            f"PAY-{p:06d}",
            fill[5],
            _at(d(12), 10, 5),
            85.40,
            "claim_balance",
            "bank",
            "pending",
            f"BK{p:06d}",
        )
    )

    referrals = [
        Referral(
            "REF-700001",
            SARAH,
            "Lakeside Family Medicine",
            "Northgate Cardiology",
            "Cardiology",
            "approved",
            d(100),
            d(100) + dt.timedelta(days=180),
            6,
        ),
        Referral(
            "REF-700002",
            MARIA,
            "Lakeside Family Medicine",
            "Pine Hill Orthopedics",
            "Orthopedics",
            "approved",
            d(80),
            d(80) + dt.timedelta(days=90),
            3,
        ),
        Referral(
            "REF-700003",
            LINDA,
            "Lakeside Family Medicine",
            "Bayview Dermatology",
            "Dermatology",
            "expired",
            d(220),
            d(40),
            2,
        ),
        Referral(
            "REF-700004",
            fill[0],
            "Lakeside Family Medicine",
            "Riverside Imaging Center",
            "Radiology",
            "approved",
            d(45),
            d(45) + dt.timedelta(days=60),
            1,
        ),
        Referral(
            "REF-700005",
            fill[6],
            "Cedar Urgent Care",
            "Northgate Cardiology",
            "Cardiology",
            "pending",
            d(5),
            None,
            None,
        ),
        Referral(
            "REF-700006",
            fill[12],
            "Lakeside Family Medicine",
            "Meadow Physical Therapy",
            "Physical therapy",
            "denied",
            d(30),
            None,
            None,
        ),
    ]

    interactions: list[Interaction] = [
        # Repeat callers (2+ calls in the last 30 days).
        Interaction(
            "INT-000001",
            MARIA,
            _at(d(6), 15, 10),
            512,
            "claims",
            "escalated to claims review",
            "Asked why MRI claim 70412 was denied. Explained CO-197. "
            "Member says the orthopedist's office said auth was handled.",
        ),
        Interaction(
            "INT-000002",
            MARIA,
            _at(d(15), 9, 40),
            305,
            "claims",
            "explained claim status",
            "MRI claim still processing at time of call.",
        ),
        Interaction(
            "INT-000003",
            MARIA,
            _at(d(120), 11, 0),
            240,
            "general",
            "sent ID card",
            "Requested replacement ID card.",
        ),
        Interaction(
            "INT-000004",
            SARAH,
            _at(d(3), 13, 25),
            420,
            "claims",
            "sent claim for reprocessing",
            "Stress test claim pending info. Called provider; itemized bill requested. "
            "Member upset this is her third call.",
        ),
        Interaction(
            "INT-000005",
            SARAH,
            _at(d(18), 16, 5),
            365,
            "claims",
            "explained claim status",
            "Claim 31845 pending provider information.",
        ),
        Interaction(
            "INT-000006",
            JAMES,
            _at(d(75), 10, 15),
            190,
            "billing",
            "updated payment method",
            "Switched premium autopay to new bank account.",
        ),
        Interaction(
            "INT-000007",
            LINDA,
            _at(d(40), 14, 0),
            610,
            "general",
            "resolved",
            "Asked how to find in network dermatologists. Sent directory link.",
        ),
        Interaction(
            "INT-000008",
            LINDA,
            _at(d(150), 12, 30),
            280,
            "billing",
            "explained bill",
            "Questions about Part B premium coordination.",
        ),
        Interaction(
            "INT-000009",
            BEN,
            _at(d(9), 11, 45),
            335,
            "claims",
            "explained claim status",
            "Asked about knee surgery auth PA-500101; told it is pending clinical notes.",
        ),
        Interaction(
            "INT-000010",
            MARK_TWIN,
            _at(d(95), 17, 0),
            150,
            "general",
            "resolved",
            "Asked for HSA contribution limits.",
        ),
        # Two filler repeat callers.
        Interaction(
            "INT-000011",
            fill[5],
            _at(d(2), 9, 5),
            270,
            "billing",
            "explained bill",
            "Pending claim balance payment; confirmed bank draft date.",
        ),
        Interaction(
            "INT-000012",
            fill[5],
            _at(d(21), 10, 50),
            330,
            "billing",
            "refund requested",
            "Believes copay was charged twice at Cedar Urgent Care.",
        ),
        Interaction(
            "INT-000013",
            fill[12],
            _at(d(11), 15, 30),
            455,
            "claims",
            "escalated to claims review",
            "Duplicate claim denial CO-18 disputed.",
        ),
        Interaction(
            "INT-000014",
            fill[12],
            _at(d(26), 8, 55),
            210,
            "claims",
            "explained claim status",
            "Asked about urgent care claim.",
        ),
    ]
    have = {i.member_id for i in interactions}
    k = 15
    for m in members:
        if m.member_id in have:
            continue
        # At most one call inside the last 30 days, the rest 40+ days apart.
        count = rng.randint(1, 5)
        day = rng.randint(8, 29) if rng.random() < 0.4 else rng.randint(40, 70)
        for _ in range(count):
            queue = rng.choice(_QUEUES)
            interactions.append(
                Interaction(
                    f"INT-{k:06d}",
                    m.member_id,
                    _at(d(day), rng.randint(8, 17), rng.choice([0, 15, 30, 45])),
                    rng.randint(90, 900),
                    queue,
                    rng.choice(_OUTCOMES[queue]),
                    f"Routine {queue} question.",
                )
            )
            k += 1
            day += rng.randint(40, 90)

    return SeedData(
        plans=list(PLANS),
        members=members,
        prior_authorizations=auths,
        claims=claims,
        payments=payments,
        referrals=referrals,
        interactions=interactions,
        kb_articles=_kb_articles(as_of),
    )


def _kb_articles(as_of: dt.date) -> list[KbArticle]:
    u = as_of - dt.timedelta(days=30)
    rows = [
        (
            "KB-CL-001",
            "How to check the status of a claim",
            "claims",
            ["claim status"],
            "Claims move through received, in review, pending info, approved, and paid or denied. "
            "Look up the claim by member and service date, then read the status and last action.",
        ),
        (
            "KB-CL-002",
            "Denial code CO-197: authorization absent",
            "claims",
            ["denial", "CO-197", "prior auth"],
            "The service needed prior authorization and none was on file. Options: ask the "
            "provider to request a retro authorization review within 60 days of the denial, "
            "or file an appeal.",
        ),
        (
            "KB-CL-003",
            "Denial code CO-242: out of network provider",
            "claims",
            ["denial", "CO-242", "network"],
            "The provider is not in the member's network. HMO and EPO plans do not cover out of "
            "network care except emergencies. Explain network rules and the appeal process.",
        ),
        (
            "KB-CL-004",
            "Denial code CO-16: missing information",
            "claims",
            ["denial", "CO-16"],
            "The claim lacked information. The provider must submit a corrected claim. Members do "
            "not need to act unless the provider asks them to.",
        ),
        (
            "KB-CL-005",
            "Denial code CO-50: medical necessity",
            "claims",
            ["denial", "CO-50"],
            "The payer judged the service not medically necessary. The member or provider can "
            "appeal with supporting clinical notes. Do not give medical opinions.",
        ),
        (
            "KB-CL-006",
            "Denial code CO-18: duplicate claim",
            "claims",
            ["denial", "CO-18"],
            "The claim duplicates one already processed. Check for an earlier paid claim with the "
            "same service date and provider before escalating.",
        ),
        (
            "KB-CL-007",
            "Claims pending provider information",
            "claims",
            ["pending info", "itemized bill"],
            "A pending info claim waits on the provider. Offer to contact the provider office and "
            "note the request on the call. Typical turnaround after receipt is 10 business days.",
        ),
        (
            "KB-CL-008",
            "How to file an appeal",
            "claims",
            ["appeal", "denial"],
            "Members have 180 days from the denial to appeal. Appeals can be filed online, "
            "by mail, or by phone. Standard decisions take up to 30 days.",
        ),
        (
            "KB-CL-009",
            "Retro authorization review",
            "claims",
            ["prior auth", "retro auth"],
            "When care happened without a required authorization, the provider can request a retro "
            "review. If approved, the denied claim is reprocessed automatically.",
        ),
        (
            "KB-CL-010",
            "Reading an Explanation of Benefits (EOB)",
            "claims",
            ["EOB"],
            "An EOB is not a bill. It shows billed amount, allowed amount, what the plan paid, and "
            "what the member may owe. Compare it with the provider's bill.",
        ),
        (
            "KB-BL-001",
            "Duplicate premium charge",
            "billing",
            ["premium", "duplicate", "refund"],
            "If autopay drafted the premium twice in one month, confirm both payments, then "
            "submit a refund request for the second draft. Refunds post in 5 to 7 business days.",
        ),
        (
            "KB-BL-002",
            "Updating autopay or payment method",
            "billing",
            ["autopay", "payment"],
            "Members can change the bank account or card for premium autopay. Changes made after "
            "the 25th apply to the following month's draft.",
        ),
        (
            "KB-BL-003",
            "Premium due dates and grace periods",
            "billing",
            ["premium", "grace"],
            "Premiums are due on the 1st. There is a 30 day grace period before coverage is "
            "suspended; claims during the grace period may pend.",
        ),
        (
            "KB-BL-004",
            "Paying a claim balance",
            "billing",
            ["claim balance", "payment"],
            "Member balances after insurance are billed by the provider, not the plan. The plan "
            "can only take payment for premiums.",
        ),
        (
            "KB-BL-005",
            "Copays, deductibles, and out of pocket maximum",
            "billing",
            ["copay", "deductible", "out of pocket"],
            "Copays are fixed amounts per visit. The deductible is what the member pays before "
            "the plan shares costs. After the out of pocket maximum the plan pays 100 percent.",
        ),
    ]
    return [KbArticle(a, t, c, tags, body, u) for a, t, c, tags, body in rows]


# Insert order respects foreign keys.
_TABLES: list[tuple[str, str]] = [
    ("plans", "plans"),
    ("members", "members"),
    ("prior_authorizations", "prior_authorizations"),
    ("claims", "claims"),
    ("payments", "payments"),
    ("referrals", "referrals"),
    ("interactions", "interactions"),
    ("kb_articles", "kb_articles"),
]
_ALL_TABLES = [t for t, _ in _TABLES] + ["agents", "calls", "transcript_lines", "assist_events"]


async def load_seed(
    conn: psycopg.AsyncConnection, *, as_of: dt.date | None = None
) -> dict[str, int]:
    """Truncate every table (incl. call records) and reload. Returns row counts."""
    data = build_seed(as_of)
    async with conn.transaction():
        await conn.execute(
            "TRUNCATE " + ", ".join(reversed(_ALL_TABLES)) + " RESTART IDENTITY CASCADE"
        )
        async with conn.cursor() as cur:
            for table, attr in _TABLES:
                rows = getattr(data, attr)
                cols = [f.name for f in fields(rows[0])]
                await cur.executemany(
                    f"INSERT INTO {table} ({', '.join(cols)}) "
                    f"VALUES ({', '.join(['%s'] * len(cols))})",
                    [astuple(r) for r in rows],
                )
    counts: dict[str, int] = {}
    for table in _ALL_TABLES:
        cur = await conn.execute(f"SELECT count(*) FROM {table}")
        counts[table] = (await cur.fetchone())[0]
    return counts
