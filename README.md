# Pipecat Agent Assist

Healthcare voice agent assist demo on Pipecat and Daily. A bot takes the call, runs intake, queues the caller, hands off to a human agent, then feeds live assist cards to the agent portal.

- Product: [docs/PRD.md](docs/PRD.md)
- Plan: [docs/PLAN.md](docs/PLAN.md)
- Agent rules: [AGENTS.md](AGENTS.md)

## Dev
```
uv sync
uv run ruff check . && uv run pytest
```
