# Deepgram Vitals: build plan (draft for Chris)

Source: docs/PRD.md (Nov 14, 2026)

## Repo layout (monorepo)
- bot/      Python 3.12, uv. pipecat-ai[daily,deepgram,silero,tone]==1.15.*, pipecat-ai-flows. Intake Flow, then listen only assist.
- server/   Python FastAPI. Daily dial-in webhook, queue service (single source of truth), agent states, websocket, Daily meeting tokens.
- portal/   React + Vite + TypeScript. @pipecat-ai/client-react + daily transport.
- packs/    YAML use case packs (claim status, account and payments, general question).
- db/       Postgres schema + mock seed. docker compose for local.
- docs/     PRD, ADRs, spike results.
- AGENTS.md coding rules the developer profile follows.

## CI (GitHub Actions, every PR)
- Python: ruff lint + format check, pytest (unit; live Daily/Deepgram tests skipped without keys).
- Portal job (eslint, tsc, vitest, build) is added by Dutch when portal/ lands; job level hashFiles() is invalid in Actions.
- Portal: eslint, tsc, vitest, build.
- Merge rule (policy, Free plan): green CI + Dutch review, squash only.

## Phase 0: Scaffold (Dutch)
- Repo skeleton, CI, AGENTS.md, PRD in docs/.
- Pipeline test card: developer adds a health endpoint + test, PR, CI, review, merge.

## Phase 1: Spike (developer cards)
- S1 Bot in a Daily room: creates room via API, joins, greets with Deepgram TTS, replies via LLM.
- S2 Per person transcription (top risk): separate Deepgram STT per participant track, every line labeled caller or agent. Written result in docs/spikes/S2.md, fallback decision if it fails.
- S3 Dial-in: Daily number webhook -> server -> new room -> bot. Greeting within 2s.
- S4 Bare browser page joins the same room with a meeting token.
- Exit: live call with caller and browser lines transcribed separately.

## Design
- Locked: docs/DESIGN.md (concept Focus), ADR 0001. Every portal card must cite DESIGN.md.

## Phase 2: Intake and queue (browser caller, ADR 0002)
- P2-1 Browser caller page starts the bot (IN-12).
- P2-2 Mock DB on Supabase Postgres: schema, migrations, seed (DC-3, DC-8).
- P2-3 Intake Flow with identity check and a pack config (IN-3 to IN-9).
- P2-4 Queue service, agent states, offers over websocket (QH-1 to QH-5, QH-10).
- Exit: a browser call finishes intake, waits in the queue, and rings an agent.

## Phase 3: Portal and handoff (DESIGN.md is binding)
- P3-1 Portal scaffold: React + Vite + TS in portal/, DESIGN.md tokens, served by the server at /agent behind the shared login, Docker build stage. Sign in and status toggle (AP-1, AP-2).
- P3-2 Incoming call and in-call shell: offer card with 20 s countdown (AP-3), accept joins the Daily room, call island and controls (AP-4), three column layout with empty assist hero.
- P3-3 Caller panel and caller history: intake summary on accept (AP-5), calls in 30 days, repeat flag, last 3 events (AP-13, DC-8).
- P3-4 Live transcript: bot goes listen only after accept, per person lines to the agent's portal only (S2 approach), trigger word underline ready.
- P3-5 Wrap up and Phase 3 exit: end call to wrap_up to available, live test ring to wrap up.
- Exit: a full call goes from ring to wrap up in the portal, matching docs/design/in-call-focus.png.

## Phases 4 and 5
Carded after Phase 3 exit: assist and connectors (next best step, packs, cards), then demo polish.
