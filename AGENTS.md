# AGENTS.md: rules for every agent working in this repo

## Roles
- Developer profile: implements kanban cards on feature branches and opens PRs. Never pushes to main, never merges, never tags releases, never edits .github/workflows.
- Dutch (manager): scopes, reviews, squash merges, releases.

## Source of truth
- Product: docs/PRD.md. Design: docs/DESIGN.md (binding for all portal UI; Studio light and dark, ADR 0005; reference mockups in docs/design/studio/). Plan: docs/PLAN.md. Decisions: docs/adr/. Spike results: docs/spikes/.
- If a card conflicts with the PRD, block the card and ask. Do not guess.

## Stack
- Python 3.12, uv workspace. Packages: bot/, server/. Pipecat pinned to 1.15.x.
- Portal: React + Vite + TypeScript in portal/, npm.
- Lint/format: ruff. Tests: pytest (Python), vitest (portal).

## Definition of done for a card
1. Acceptance criteria in the card body all met.
2. Tests added or updated. `uv run ruff check . && uv run ruff format --check . && uv run pytest` passes locally. Portal: `npm run lint && npm run typecheck && npm test && npm run build` when portal changes.
3. One focused PR, title `<card-id>: <summary>`, body: what, why, how tested (paste real command output), open items.
4. CI green on the PR.

## Hard rules
- No secrets in code, commits, logs, PRs. Keys come from environment variables; document new ones in .env.example.
- Mock data only. No real patient data anywhere.
- Tests that need live Daily/Deepgram/LLM keys are marked `@pytest.mark.live` and skipped by default.
