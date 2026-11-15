# ADR 0003: Product name Deepgram Vitals

Date: Nov 15, 2026 · Decided by: Chris Catalano · Recorded by: Developer (card P2-7)

## Context
- The project started as "Pipecat Agent Assist". That describes the category, not the product, and it is what callers, agents, and demo viewers saw: the page titles, the portal lockup, the bot's Daily display name ("Intake Bot"), and the FastAPI title ("Agent Assist Server").
- Chris named the product "Deepgram Vitals" on Nov 15.

## Decision
1. **Name.** The product is "Deepgram Vitals". In the portal lockup next to the Deepgram wordmark it is shortened to "Vitals". Brand copy rules from DESIGN.md apply: sentence case, no exclamation points.
2. **Scope: everything a person sees.**
   - Docs: README title and intro, PRD title and summary ("Deepgram Vitals: healthcare voice agent assist"), PLAN title, DESIGN header lockup text and the mockup header (`docs/design/in-call-focus.html`), caller.md text that quotes the bot name.
   - UI: login page title and lockup, caller page title and heading, spike agent page title, FastAPI app title.
   - Bot: Daily display name "Vitals Assistant" (`BOT_NAME` in the bot, `BOT_USER_NAME` in the server; they must match because the meeting token's `user_name` overrides the bot's own name).
   - Greeting: "Hi, thanks for calling. You're speaking with Deepgram Vitals, an AI assistant. I'll ask a few quick questions, and then a person will join the call to help you." IN-2 is still met (AI assistant, a person will join).
   - Package descriptions in the three `pyproject.toml` files.
3. **Not renamed, on purpose.**
   - Python distributions and modules (`agent-assist-bot`, `agent-assist-server`, `agent_assist_bot`, `agent_assist_server`), the workspace name `pipecat-agent-assist`, and file paths. Renaming them touches every import, the lockfile, the Dockerfile and CI, for no user-visible gain.
   - Environment variables (`AGENT_ASSIST_SERVER_URL`, `AGENT_ASSIST_BOT_KEY`), the `agent_assist.queue` app message type, the `/healthz` `service` value, and database names. They are wire or deployment contracts that running environments and the portal already depend on.
   - Historical text in `docs/spikes/` (for example S1 recorded that the bot joined as "Intake Bot"). Those are results from that date and stay as written.
   - The PRD's description of the product category ("healthcare voice agent assist").
   - The GitHub repo name and remote URLs. Dutch renames the repo after merge.

## Consequences
- The greeting is 17 characters (2 words, "Deepgram Vitals,") longer, under a second more TTS before the first question. The greeting latency budget (time to first bot audio) is unaffected.
- Live tests and the test harness wait for "Vitals Assistant"; they import `BOT_NAME` where they can so the name lives in one place per package.
- A later rename of packages or env vars, if wanted, is a separate card with its own migration.
