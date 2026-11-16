# Agent portal

React 18 + Vite + TypeScript. Served by the server at `/agent` behind the shared login
(`server/agent_assist_server/portal.py`). Design: `docs/DESIGN.md` (binding).

```
npm ci
npm run dev        # http://localhost:5173/agent/, proxies /agents, /queue, /ws to :8000
npm run lint
npm run typecheck
npm test
npm run build      # portal/dist, which the server serves at /agent
```

Layout:

- `src/styles/tokens.css`: DESIGN.md tokens as CSS variables (colors, fonts, glass,
  radii, glows, grain) plus shared primitives (`.glass`, `.label`, `.chip`, `.btn`).
- `src/api/`: typed client for the queue REST API and `/ws/queue` (docs/queue.md).
  `QueueSocket` reconnects with exponential backoff and stops on close code 4401
  (login expired). Each new connection starts with a fresh snapshot.
- `src/state/queueState.ts`: reducer that keeps the client copy of the queue.
- `src/components/`: top bar, sign in (AP-1), idle home with status toggle (AP-2).

Fonts are self hosted through `@fontsource` (Work Sans 400/500/600, Azeret Mono
400/500). Icons: `lucide-react`, stroke 1.8.
