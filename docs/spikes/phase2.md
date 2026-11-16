# Phase 2 exit: voice intake -> queue -> agent ring (P2-5)

> Note (Nov 16, P5-10): demo IDs and names changed after this was written. Member IDs
> are now 6 digits (Maria Alvarez EVM100231 is now Maria Lopez 418302) and claim
> numbers 5 digits (CLM-900001 is now 70412); see docs/db.md. The results below are
> kept as measured, with the old IDs.

Date: 2026-11-15. Card t_4b9f753b. Pipecat 1.15.0, Deepgram nova-3 STT + aura TTS,
Anthropic (default `create_llm` model), Daily `dg-chriscat`, local Postgres 16
(throwaway schema per run, migrated and seeded).

## What runs

`server/tests/test_e2e_live.py` (`@pytest.mark.live`):

```bash
uv run pytest -m live server/tests/test_e2e_live.py -s -v
```

Needs `DAILY_API_KEY`, `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`.

1. The real server runs under uvicorn on a local port. `POST /calls/browser` creates the
   Daily room and starts the bot process. The bot inherits `AGENT_ASSIST_SERVER_URL` (so its
   intake hook is `QueueHandoff`) and `DATABASE_SCHEMA` (the throwaway schema, so identity
   is checked against the seed members).
2. A daily-python caller (S1 harness) answers as demo persona 1, Maria Alvarez, with
   Deepgram TTS audio. The script is in `bot/tests/harness/intake_script.py`, shared with
   `bot/tests/test_live_intake.py`. The next line is picked from the saved intake fields.
3. When the caller confirms the read back, the Flow calls `QueueHandoff`, which posts to
   `POST /queue/calls`. Nothing in the test calls the hook by hand.
4. The call waits in the queue for 5 s with no agent available.
5. A websocket agent on `/ws/queue` signs in and goes available. It gets the offer with
   the intake summary (20 s window), accepts 2 s later, and joins the room with the token
   from the `accepted` reply.
6. The bot says "Connecting you now." The caller then asks a question and the bot does not
   answer for 10 s (BotGate). Daily presence shows caller, bot, and agent.
7. The caller leaves, the bot exits 0, and the room is deleted.

`server/tests/test_e2e_intake_queue.py` is the offline version of the same wiring (CI). It
runs the real Flow handlers through `QueueHandoff` into the real app over ASGI, then an
agent gets the offer and accepts it. Daily is mocked.

## Timings

Intake duration runs from both the caller and the bot being in the room to the call
appearing in the queue (`queued_at`). Accept to agent joined runs from the agent's
`accept` message to the agent's daily-python client reporting itself joined.

| Run | Result | Intake (s) | Identity | Queue wait (s) | Available to offer (s) | Accept to token (s) | Accept to agent joined (s) | Accept to "Connecting you now." (s) |
|---|---|---|---|---|---|---|---|---|
| 1 | pass | 100.1 | verified | 10.6 | 0.005 | 0.43 | 1.52 | 1.10 |
| 2 | fail (see below) | 109.9 | not_verified | - | - | - | - | - |
| 3 | fail: intake over 120 s | 132.4 | not_verified | 10.5 | 0.007 | 0.30 | 1.74 | 1.16 |
| 4 | pass | 104.1 | not_verified | 11.1 | 0.004 | 0.35 | 1.90 | 1.79 |
| 5 | fail: Daily transport | - | - | - | - | - | - | - |
| 6 | pass | 108.5 | verified | 10.7 | 0.01 | 0.41 | 1.58 | 1.30 |
| 7 | pass | 105.5 | verified | 10.4 | 0.00 | 0.31 | 1.67 | 1.39 |

Runs 1 to 5 were on the development revision. Runs 6 and 7 were on the PR commit, after
`test_e2e_live.py` stopped requiring a verified identity.

Queue wait = 5 s with no agent + the time for the agent to sign in and go available + the
time to read the offer. The test holds 5 s on purpose.

Queue to agent leg (runs 1, 3, 4, 6, 7): offer under 10 ms after the agent goes
available, token under 0.5 s after accept, agent in the room 1.5 to 1.9 s after accept,
connecting line 1.1 to 1.8 s after accept. The 20 s offer window was never at risk.

Intake leg: 100 to 132 s against the PRD's 2 minute target (100 to 110 s when nothing is
re-asked). P2-3 measured 111.5 s. The fixed parts are long. The greeting is about 9 s,
the read back alone is about 19 s (the bot spells out the claim number), and the handoff
line is about 4 s. One re-ask pushes intake over 120 s (run 3).

## Failures

- Run 2 (an earlier revision of the test asserted `identity_result == verified`) and run 3:
  STT split "E V M, one zero zero, two three one" across endpoints and dropped the "one".
  The LLM passed `EVM00231` to `verify_identity`. In run 3 the bot re-asked once (that
  cost about 30 s and pushed intake to 132 s), then got `EVM00231` again.
- Run 4: STT heard "Marie Alvarez", so identity was `not_verified` with the right member
  ID. The call was still queued with the `identity_not_verified` flag, and the agent saw it
  on the offer. That is the PRD behaviour, so the test passed.
- Run 5: Daily's media transport failed for the bot about 40 s in ("recv transport changed
  to failed", then websocket broken pipe). The caller's audio stopped reaching STT, the
  intake stalled, and the bot ended on its 120 s idle timeout. Load average was 2.2, so
  the host was not overloaded. This failure is in Daily's transport, not in our code.

The e2e test checks that the queue carries exactly what intake recorded (identity result,
member ID, flags). It does not require identity to verify. Verification by voice is gated
by `bot/tests/test_live_intake.py`. The test still asserts the PRD's 120 s intake target,
so a slow intake fails the run (run 3).

## Open items

- Identity by voice is fragile with spelled member IDs. In the 7 e2e runs, STT misheard
  the member ID twice and the name once. `bot/tests/test_live_intake.py`, re-run once on
  the PR commit after its script and turn helpers moved into the harness, finished intake
  in 105.4 s but failed its `verified` assertion: STT heard "Marie Alvarez" and
  "e d m one zero zero two three one", so the result was `not_found`. Candidates:
  Deepgram keyterms or `numerals` for the identity step, a longer endpointing window
  while the identity node is active, or a confirm-back of the member ID before
  `verify_identity`.
- Intake runs close to the 2 minute target. A shorter read back (no spelled-out claim
  number) would save about 10 s.
