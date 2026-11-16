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
- `src/components/`: top bar, sign in (AP-1), idle home with status toggle (AP-2),
  incoming call card (AP-3), in-call shell (AP-4).
- `src/call/`: call audio. `media.ts` is the seam; `dailyMedia.ts` joins the
  caller's Daily room with the Pipecat client SDK and the Daily transport (audio
  only, mic on), loaded lazily on the first accept. Tests use `test/fakeMedia.ts`.

Call flow (docs/queue.md):

- An `offer` for this agent (or, after a reload, the snapshot's `offer_call_id`)
  shows the incoming card with a countdown to `expires_at`. Enter accepts, Esc
  declines.
- Accept posts `/agents/{id}/accept`, shows the intake summary from the reply at
  once (QH-7), then joins the room with the returned token (QH-6).
- Decline posts `/agents/{id}/decline`. A decline or timeout comes back as
  `offer_missed` and the server sets the agent not available; the home shows why.
- Mute and hold only change the local mic (hold is local in v1, no status change).
  End leaves the room and sets `wrap_up`. If the caller hangs up (`call_ended`),
  the portal leaves the room; the server already moved the agent to wrap up.

Fonts are self hosted through `@fontsource` (Work Sans 400/500/600, Azeret Mono
400/500). Icons: `lucide-react`, stroke 1.8.

Screen gallery (P5-3, dev only): `npm run dev` also serves
`/agent/screens.html?screen=<name>&theme=<light|dark>` (`src/dev/screens.tsx`), every
screen in a fixed state with mock data: sign in, home, incoming call, in call
(listening, suggestion, accepted), wrap up, and the empty, loading, and error states.
It is not in the production build. To screenshot them all in both themes:

```
PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright python portal/scripts/shoot_screens.py OUT_DIR \
    [--size 1440x900] [--only in-call-suggestion,wrap-up] [--port 5173]
uv run --with pillow python portal/scripts/pair_sheets.py BEFORE_DIR AFTER_DIR OUT_DIR NAME...
```
