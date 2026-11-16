# Mock database

PRD DC-3, DC-8. ADR 0002. Plain Postgres (15+), reached through `DATABASE_URL`
(Supabase Session pooler URI for the demo, any Postgres otherwise). All data is
fake and deterministic. No real patient data, ever. Supabase is not under a BAA.

## Commands

```
uv run --env-file .env python -m agent_assist_server.db migrate
uv run --env-file .env python -m agent_assist_server.db seed
```

- `migrate` applies `db/migrations/*.sql` in name order, once each, and records
  them in `schema_migrations`. Re-running is a no-op.
- `seed` truncates every table (including `calls`, `transcript_lines`, and `assist_events`) and
  reloads the fixed data set. Run it before a demo; Supabase free projects pause
  after a week idle.
- `--schema NAME` targets a schema other than the server default (tests use this).
- `--as-of YYYY-MM-DD` pins the reference date. Seed dates are relative to it
  (default: today, UTC) so "called in the last 30 days" stays true whenever the
  demo is reseeded. For a fixed date the output is identical every run.

Code: `server/agent_assist_server/db/` (`connect()` helper, `migrate.py`, `seed.py`).
`connect(schema=...)` sets `search_path` with `SET`, which works through the
Supabase pooler.

## Tables

Data systems (read only for connectors, DC-2):

| Table | Key | Notes |
| --- | --- | --- |
| `plans` | `plan_id` | HMO, PPO, EPO, HDHP, Medicare Advantage. Deductible, OOP max, copays, premium, `requires_referral`. |
| `members` | `member_id` | `first_name`, `last_name`, `dob`, `phone`, `plan_id`, coverage dates. Index on lower(last, first) + dob for identity checks. |
| `claims` | `claim_id` | `status` in received, in_review, pending_info, approved, paid, denied. `denial_code` (CARC style, e.g. CO-197) is set if and only if denied (CHECK). Optional `auth_id`. |
| `payments` | `payment_id` | Premiums, copays, claim balances, refunds. `status` posted, pending, reversed. |
| `referrals` | `referral_id` | From and to provider, specialty, status, validity, visits allowed. |
| `prior_authorizations` | `auth_id` | Service, provider, status pending, approved, denied, expired, validity window. |
| `interactions` | `interaction_id` | Past calls (DC-8): `started_at`, `duration_seconds`, `queue`, `outcome`, `notes`. Caller history and repeat caller detection. |
| `kb_articles` | `article_id` | 15 billing and claims articles, `category`, `tags`. |

Call records (written by bot and server):

| Table | Key | Notes |
| --- | --- | --- |
| `calls` | `call_id` (uuid) | `session_id` (unique), `status` intake, ready_for_queue, queued, with_agent, ended, abandoned. `member_id`, `intake` jsonb (answers saved as captured, IN-5), `identity_result` verified, close_match, not_verified, not_found, incomplete (0009), `identity_decision` jsonb (IN-13, 0009): the agent's `{decision, agent_id, agent_name, at}` on the verify hero, `flags` text[], `created_at`, `updated_at`, `queued_at`, `answered_at`, `ended_at`. `phi_reveals` jsonb (AP-17): one `{field, agent_id, at}` entry each time the agent reveals the masked member ID or DOB; never the value. |
| `transcript_lines` | `line_id` | `call_id` (cascade delete), `speaker_role` caller, bot, agent, `text`, `ts`, `tone`. |
| `assist_events` | `event_id` | `call_id` (cascade delete), `kind` card_sent, accept, dismiss, pin, unpin, rate, guardrail, identity_decision, `card_id`, `data` jsonb, `created_at`. What the assist showed and what the agent did (P4-3, DC-9; docs/assist.md). |

## Seed contents

Row counts for any `as_of` date: plans 6, members 25, prior_authorizations 5,
claims 43, payments 69, referrals 6, interactions 67, kb_articles 15, calls 0,
transcript_lines 0.

P5-10 (Chris, Nov 16): the data is set up for speech to text in demos.

- Member IDs are 6 digits, read in two groups of three (418 302): no leading 0, no
  digit twice in a row, and no group of three shared by two members. Claim numbers are
  5 digits, read as two and three (70 412), and never part of a member ID. Payment,
  referral, auth, and interaction IDs stay internal (PAY-, REF-, PA-, INT-).
- Names are short and common, except two deliberately hard names for the identity
  verification demo: Linda Nguyen and Aisha Patel. Filler member IDs are generated
  (seeded RNG) under the same rules, so the seed stays deterministic.
- 25 members across all 6 plans. Phones use the fictional 555-0100 to 555-0199 range.
- Claims include 7 denials with codes CO-16, CO-18, CO-50, CO-197, CO-242.
- Every member has 1 to 5 past interactions. Four members called 2+ times in
  the last 30 days (repeat callers): 418302 (Maria Lopez), 639251 (Sarah Miller),
  165435, 413709.
- Two members are named Mark Hill (285916, born 1975-10-06, and 396127, born
  1986-02-09), so identity needs name + DOB + member ID, not name alone.

## Demo personas

The scripted demo callers in the seed (`seed.DEMO_PERSONAS`). The Demos page
(docs/demos.md) adds scripts, chips, and a caller who is not a member.

| Name | DOB | Member ID | Plan | Why they call |
| --- | --- | --- | --- | --- |
| Maria Lopez | 1984-03-12 | 418302 | Evergreen HMO Silver | MRI claim 70412 denied, CO-197 (no prior authorization). Repeat caller (2 calls in the last 30 days). Next best step: retro authorization review. |
| James Carter | 1979-07-04 | 527194 | Summit PPO Gold | Charged twice for this month's premium (duplicate autopay draft). Account and payments pack. |
| Sarah Miller | 1988-06-15 | 639251 | Evergreen HMO Gold | Cardiac stress test claim 31845 stuck in pending_info for about 3 weeks; provider has not sent the itemized bill. Frustrated repeat caller (tone tip). |
| Linda Nguyen | 1958-04-22 | 741526 | Evergreen Medicare Advantage Plus | Hard name. Dermatology claim 52976 denied CO-242 (out of network, referral expired); how to appeal. STT often mishears the last name: close match, verification hero. |
| Aisha Patel | 1995-08-09 | 174839 | Evergreen HMO Silver | Hard name. Asks what an EOB is for claim 19374 (KB-CL-010). Often heard as Ayesha: close match. |
| Ben Cole | 1990-05-17 | 852617 | Harbor EPO Bronze | Status of knee surgery prior auth PA-500101 (pending). Scripted to give a wrong DOB (May 7) first, so identity returns not_verified and the call is flagged. |
| David Brooks | 1971-09-25 | 263048 | Summit HDHP with HSA | How the deductible and out of pocket maximum work (KB-BL-005). |
| Emily Stone | 1983-02-14 | 307495 | Summit PPO Gold | Emergency guardrail: describes chest pain at intake, gets the 911 line. |
| Mark Hill | 1975-10-06 | 285916 | Evergreen HMO Gold | Urgent care claim 46183 in review. The other Mark Hill (1986-02-09, 396127) is on Summit HDHP with HSA. |

After this change, reseed the demo database (`python -m agent_assist_server.db seed`);
the deploy release step only migrates.

## Tests

`server/tests/test_db.py` needs `DATABASE_URL` and skips without it. CI provides
a Postgres 16 service. Each test creates a throwaway `test_<hex>` schema, runs
migrations and the seed there, and drops it, so the public schema and the demo
data are never touched. `server/tests/test_db_seed_data.py` checks the seed
shape in memory and always runs.

## Supabase
- The demo database is Supabase Postgres (ADR 0002). Connect with the Session pooler URI in `DATABASE_URL` (the direct host is IPv6 only).
- Migration 0002 enables row level security and revokes the `anon` and `authenticated` roles, so the tables are not readable through the Supabase REST API with the publishable key. Keep it that way for every new table.
