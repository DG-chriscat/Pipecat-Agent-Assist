# ADR 0002: Browser caller for the PoC, Supabase for the mock database

Date: Nov 15, 2026 · Decided by: Chris Catalano · Recorded by: Dutch

## Context
- Spike phase is done: S1 (bot talks), S2 (per person transcription, WORKS), S4 (browser joins with a meeting token) are merged. The dial-in webhook (IN-1 server path) is merged with mocked tests.
- A Daily phone number is available now (card on file), but Chris chose not to buy one yet. The live phone test (S3) stays deferred.
- Docker is not available on the build host, so a local Postgres server cannot run there. Chris set up a Supabase account.

## Decision
1. **Browser caller.** For the PoC the caller joins from a browser page instead of a phone. The server creates the room, starts the same bot the dial-in path starts (same `build_worker`, no dial-in settings), and gives the caller page a meeting token. Everything after the caller joins (intake, queue, handoff, assist) is identical for phone and browser callers.
2. **Phone stays a drop in.** When a number is bought, Daily's `room_creation_api` points at the existing `/daily/dialin` webhook. No bot or queue changes.
3. **Supabase Postgres is the mock database.** The app talks plain Postgres (`DATABASE_URL`, Session pooler URI), not the Supabase client SDK, so any Postgres works later. Mock data only.
4. **Schema as SQL migrations in `db/`**, applied by a small script. Seed data is deterministic and fake.
5. **Tests:** CI runs a Postgres service container. DB tests skip when `DATABASE_URL` is unset. Tests create and drop their own schema, so they never touch the demo data.

## Consequences
- New requirement IN-12: browser caller page (Must for the PoC). IN-1 live verification moves to Later.
- `DATABASE_URL` joins `.env.example`. It is a secret and lives only in `.env` files.
- Supabase free projects pause after a week idle. Run the seed script before a demo.
- No real patient data, same as before. Supabase is not under a BAA and must not hold PHI.
