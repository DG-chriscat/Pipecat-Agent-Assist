# Pipecat Agent Assist: Build Plan (draft for Chris)

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

## Phases 2 to 5
Broken into cards after the spike, per PRD milestones: intake and queue, portal and handoff, assist and connectors, demo polish.
