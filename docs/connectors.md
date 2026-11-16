# Connectors

PRD DC-1 to DC-5, DC-8. Card P4-1. Read only tools over the mock DB (docs/db.md)
that the assist engine (P4-3) gives the LLM.

Code: `bot/agent_assist_bot/connectors/`

| File | What |
| --- | --- |
| `base.py` | `Connector` spec, `Tool` (schema and argument checks), `ConnectorSession` (scoping, timeout, cache, logging, Pipecat handlers). |
| `catalog.py` | The 8 v1 connectors (`CONNECTORS`). Add a connector here. |
| `postgres.py` | `PostgresReader`: async psycopg, read only session. |

The package lives in the bot because the assist loop runs in the bot process. It
depends only on the bot (`agent_assist_bot.calls` for env names and member id
normalising), not on the server.

## Usage

```python
from agent_assist_bot.connectors import ConnectorSession, PostgresReader

reader = PostgresReader.from_env()  # DATABASE_URL, optional DATABASE_SCHEMA
session = ConnectorSession(reader, call_id=call_id, member_id=verified_member_id)

await session.call("claims_search", {"status": "denied"})  # plain call
session.tool_schemas(["claims", "kb_articles"])  # JSON tool schemas for a pack
tools = ToolsSchema(standard_tools=session.function_schemas(pack.allowed_connectors))
```

Create one `ConnectorSession` per call, after identity: its cache and member scope
belong to that call. `function_schemas()` returns Pipecat `FunctionSchema`s with the
handler attached, so putting them in the `LLMContext` tools is all the registration
needed. `close()` the reader at the end of the call.

## Rules

- One shape (DC-1). Every connector has `<name>_search` (filters, `limit` 1 to 25,
  default 10) and `<name>_get` (its id). The id param is required on get; every
  search param is optional.
- Read only (DC-2). The reader sets `default_transaction_read_only = on` on its
  connection; a write fails with `ReadOnlySqlTransaction` (tested). SQL is built
  from the fixed spec in `catalog.py`; argument values are always bound
  parameters, never interpolated.
- Member scoping. Claims, payments, referrals, prior authorizations, interactions,
  and members are scoped: the session binds its own `member_id` into every query,
  so another member's rows never come back (a get for someone else's id returns
  `found: false`). The LLM does not pass a member id. If it sends one anyway, a
  matching id is ignored and a different one returns `error: member_scope`.
  A scoped tool on a call with no verified member returns `error: no_member`.
  Plans and KB articles are reference data and not scoped.
- Timeout (DC-4). 2 s per call (`asyncio.wait_for`), backed by a 2 s server side
  `statement_timeout`. On timeout the reader drops the connection and the next
  call reconnects.
- Cache (DC-4). Successful results are cached in the session for the rest of the
  call, keyed by tool name and normalised arguments (argument order does not
  matter). Errors and timeouts are not cached.
- Logging (DC-5). One line per call:

  ```
  [connector] call_id=... tool=claims_search args=status=denied status=ok rows=1 bytes=412 ms=8.3 cache=miss
  ```

  `args` logs enums, ids, dates, and limits verbatim. Free text (`provider`, KB
  `query`) is logged as its length only, for example `query=<23 chars>`. Result rows,
  member ids, and the database URL are never logged. A database error logs only
  the exception type.
- Errors never raise to the LLM. They come back as
  `{"tool", "source", "error", "message"}` with `error` one of `bad_args`,
  `unknown_tool`, `member_scope`, `no_member`, `timeout`, `unavailable`.

## Result shape

Compact JSON shaped for cards: ids, ISO dates (`YYYY-MM-DD`), UTC timestamps
(`2026-11-01T06:02:00Z`), amounts as numbers, status, denial code, and the source
label for the card footer.

```json
{"tool": "claims_search", "source": "Claims system", "count": 1,
 "items": [{"claim_id": "70412", "service_date": "2026-10-12", "status": "denied",
            "denial_code": "CO-197", "billed_amount": 1850.0, "...": "..."}]}

{"tool": "claims_get", "source": "Claims system", "found": true, "item": {"...": "..."}}
```

Search returns newest first unless noted. Get returns every field below; search
returns the search fields (smaller rows for the LLM).

## Tools

### Members (source: Member system, scoped)

The current caller's member record with plan.

- `members_search`: `limit`. Returns the caller (one row).
- `members_get`: `member_id` (only the caller's id finds a row).
- Fields: `member_id`, `name`, `dob`, `plan_id`, `plan_name`, `plan_type`,
  `coverage_start`, `coverage_end`.

### Plans (source: Benefits system, not scoped)

- `plans_search`: `plan_type` (HMO, PPO, EPO, HDHP, Medicare Advantage, Medicaid),
  `limit`. Ordered by `plan_id`.
- `plans_get`: `plan_id`, e.g. `PLN-HMO-SILVER`.
- Fields: `plan_id`, `name`, `plan_type`, `requires_referral`, `deductible`,
  `out_of_pocket_max`, `pcp_copay`, `specialist_copay`, `monthly_premium`.

### Claims (source: Claims system, scoped)

- `claims_search`: `status` (received, in_review, pending_info, approved, paid,
  denied), `denial_code` (e.g. CO-197, case insensitive), `service_from`,
  `service_to` (dates), `provider` (part of the name, case insensitive), `limit`.
  Newest service date first.
- `claims_get`: `claim_id`, e.g. `70412`.
- Search fields: `claim_id`, `service_date`, `provider`, `service`,
  `billed_amount`, `status`, `denial_code`, `denial_reason`, `received_on`,
  `processed_on`.
- Get adds: `allowed_amount`, `member_responsibility`, `auth_id`.

### Payments (source: Billing system, scoped)

- `payments_search`: `kind` (premium, copay, claim_balance, refund), `status`
  (posted, pending, reversed), `method` (card, bank, check, autopay), `paid_from`,
  `paid_to` (dates, inclusive), `limit`. Newest first.
- `payments_get`: `payment_id`, e.g. `PAY-000067`.
- Fields: `payment_id`, `paid_at`, `amount`, `kind`, `method`, `status`, `reference`.

### Referrals (source: Referral system, scoped)

- `referrals_search`: `status` (pending, approved, denied, expired), `specialty`
  (part of the name), `limit`. Newest first.
- `referrals_get`: `referral_id`, e.g. `REF-700002`.
- Fields: `referral_id`, `from_provider`, `to_provider`, `specialty`, `status`,
  `created_on`, `valid_to`, `visits_allowed`.

### Prior authorizations (source: Utilization management, scoped)

- `prior_authorizations_search`: `status` (pending, approved, denied, expired),
  `limit`. Newest request first.
- `prior_authorizations_get`: `auth_id`, e.g. `PA-500101`.
- Fields: `auth_id`, `service`, `provider`, `status`, `requested_on`, `decided_on`,
  `valid_from`, `valid_to`, `notes`.

### Interactions (source: Interaction history, scoped, DC-8)

Past calls, for caller history and repeat caller detection.

- `interactions_search`: `queue` (claims, billing, general), `since` (date),
  `limit`. Newest first.
- `interactions_get`: `interaction_id`, e.g. `INT-000001`.
- Fields: `interaction_id`, `started_at`, `duration_seconds`, `queue`, `outcome`,
  `notes`.

The portal's caller panel (P3-3, `server/agent_assist_server/caller_api.py`) keeps
its own SQL: it is a server endpoint that merges interactions, claims, and referrals
into one timeline, which is a different shape from a connector. Both read the same
`interactions` table.

### KB articles (source: Knowledge base, not scoped)

- `kb_articles_search`: `query` (keywords), `category` (billing, claims), `limit`.
  Keyword search over title, tags, and body: the query is split into terms (stop
  words dropped, up to 8), each term scores 3 in the title, 2 in a tag, 1 in the
  body, and articles with a score above 0 come back best first. Without `query`,
  ordered by `article_id`.
- `kb_articles_get`: `article_id`, e.g. `KB-CL-002`.
- Search fields: `article_id`, `title`, `category`, `tags`, `summary` (first 160
  characters of the body).
- Get adds: `body`, `updated_on`.

## Demo persona checks (seed, tested)

| Persona | Call | Result |
| --- | --- | --- |
| Maria Lopez, 418302 | `claims_search {"status": "denied"}` | 70412, MRI, CO-197 |
| Maria Lopez | `prior_authorizations_search {}` | none on file |
| Maria Lopez | `kb_articles_search {"query": "retro authorization"}` | KB-CL-009 first |
| James Carter, 527194 | `payments_search {"kind": "premium", "paid_from": "<1st of month>"}` | two 610.00 autopay drafts, 2 minutes apart |
| Ben Cole, 852617 | `prior_authorizations_search {"status": "pending"}` | PA-500101 knee surgery |

## Tests

- `bot/tests/test_connectors.py`: fake reader. Cache hits, per call cache, timeout
  path, error results, log line contents and no free text in logs, argument
  checks, member id binding, bound parameters, tool schemas, Pipecat handlers,
  and this doc (every tool, param, and field must appear here).
- `bot/tests/test_connectors_db.py`: needs `DATABASE_URL` (CI has Postgres). One
  throwaway `test_<hex>` schema, migrated and seeded, dropped after. Every connector
  against the seed, member scoping, read only enforcement, timeout against Postgres
  and reconnect, server side statement timeout.
