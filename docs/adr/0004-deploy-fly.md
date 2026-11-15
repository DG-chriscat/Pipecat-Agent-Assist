# ADR 0004: Deploy the PoC on Fly.io

Date: Nov 15, 2026 · Decided by: Chris Catalano · Recorded by: Dutch

## Context
Chris wants to test from his own machine and with a second person on another machine, and the Daily phone number (+1 612 746 7133) needs a public URL for the pinless dial-in webhook. Chris created the Fly app `deepgram-vitals` in the deepgram org.

## Decision
- One Docker image holds the server and the bot. The server spawns one bot process per call (same as local), so one Fly machine runs everything. Daily carries the audio; Fly only runs code.
- Exactly one machine (`--ha=false`). The queue lives in memory (docs/queue.md), so a second machine would split it.
- shared-cpu-4x, 4 GB. One bot uses about a third of a core (S2), and the PRD target is 5 calls.
- Region ord (Chicago), central US, close to Daily's US SFUs and the Minneapolis number.
- Machine stops when idle and starts on the next request; first request after idle pays a cold start.
- The database is Supabase (ADR 0002). `release_command` runs migrations on every deploy; never seeds.
- Login (APP_PASSWORD) is required in production. All keys are Fly secrets.
- GitHub Actions deploys main after CI passes (`.github/workflows/deploy.yml`) with an app scoped deploy token in the repo secret FLY_API_TOKEN. Manual `flyctl deploy` from Dutch also works.

## Consequences
- A deploy restarts the machine and drops live calls. Deploy when no demo is running.
- The Fly MCP could not pass the deepgram org's SSO check (Nov 15), so ops use flyctl with the deploy token.
- Pipecat Cloud for bots stays an option once we need more than one machine.
