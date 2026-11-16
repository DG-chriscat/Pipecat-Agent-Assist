# Deepgram Vitals: server + per call bot processes in one image (ADR 0004).

# ---- portal: build the agent portal (React + Vite) into portal/dist ----
FROM node:22-bookworm-slim AS portal
WORKDIR /portal
COPY portal/package.json portal/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY portal/ ./
RUN npm run build

# ---- app: Python server and bot ----
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Dependencies first for layer caching.
COPY pyproject.toml uv.lock ./
COPY server/pyproject.toml server/pyproject.toml
COPY bot/pyproject.toml bot/pyproject.toml
RUN mkdir -p server/agent_assist_server bot/agent_assist_bot \
 && touch server/agent_assist_server/__init__.py bot/agent_assist_bot/__init__.py \
 && uv sync --frozen --no-dev --all-packages --no-install-workspace

COPY server server
COPY bot bot
COPY packs packs
COPY db db
RUN uv sync --frozen --no-dev --all-packages \
 && python -c "import agent_assist_server.app, agent_assist_bot.bot"

# Served at /agent by agent_assist_server.portal (default path <repo>/portal/dist).
COPY --from=portal /portal/dist portal/dist
ENV PORTAL_DIST=/app/portal/dist

# Server spawns bots with sys.executable, so they share this venv.
ENV TRUSTED_CLIENT_IP_HEADER=Fly-Client-IP
EXPOSE 8080
CMD ["uvicorn", "agent_assist_server.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
