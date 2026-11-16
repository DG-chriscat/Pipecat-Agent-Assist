# Phase 3 exit: ring to wrap up (P3-5)

Date: 2026-11-16. Card t_f66342b1. Pipecat 1.15.0, Deepgram nova-3 STT + aura TTS,
Anthropic, Daily `dg-chriscat`, local Postgres 16 (throwaway schema per run), headless
Chromium 1600x1000.

## What runs

`server/tests/test_wrap_up_live.py` (`@pytest.mark.live`):

```bash
P3_SCREENSHOT_DIR=docs/screenshots/p3-5 \
  uv run pytest -m live server/tests/test_wrap_up_live.py -s -v
```

Needs `DAILY_API_KEY`, `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, a built
portal (`npm run build` in `portal/`) and Playwright Chromium.

1. The real server runs under uvicorn. `POST /calls/browser` creates the Daily room and
   starts the bot. A daily-python caller (S1 harness) answers intake as Maria Alvarez.
2. Intake hands off to the queue. Two portal agents (Ana, Ben) sign in in headless
   Chromium with a fake mic that speaks Deepgram TTS audio.
3. Ana goes available, the card rings, she presses Enter. The test times Enter to the
   first audio from the caller played in the page (accept to hearing the caller).
4. Live transcript: the caller and Ana each speak; lines show in Ana's transcript.
5. QH-9: Ana closes her tab without End. When Daily reports her gone, the bot posts
   `agent-left`; after a 3 s grace the server puts the call back at the front of the
   queue and sets Ana to not available. The bot speaks the requeue line to the caller.
6. Ben goes available, gets the ring, accepts (timed again), talks, and presses End.
7. P3-5: the wrap up view shows (summary placeholder, reason codes from the pack, notes).
   The bot says goodbye and exits; the server closes the room 6 s later.
8. Ben picks a reason code, writes notes, saves, and is available again. The `calls` row
   holds `ended_by`, the reason code, notes, and the agent; transcript lines are saved.

`server/tests/test_wrap_up_api.py`, `test_queue_service.py`, `test_queue_api.py`,
`test_queue_store.py`, `bot/tests/test_queue_hold.py`, `bot/tests/test_bot.py`, and the
portal `CallFlow.test.tsx` / `WrapUp.test.tsx` / `useCall.test.ts` cover the same paths
offline (CI).

## Timings

Accept to hearing the caller runs from Enter on the ringing card to the first remote
audio track playing in the agent's page. Portal timeline marks (`performance.mark`,
`vitals:*`) break it down.

| Run | Change | Ana (s) | Ben (s) | Median (s) |
|---|---|---|---|---|
| 1 | baseline (token minted on accept, SDK loaded on accept) | 4.74 | 4.06 | 4.40 |
| 4 | token minted when the offer rings; SDK and mic warmed while ringing | 2.17 | - | - |
| 5 | same | 1.45 | 2.09 | 1.77 |
| 6 | same | 1.98 | 2.24 | 2.11 (fails the 2 s median check) |

Run 5 timeline (Ana): accepted +0.04 s, join +0.04 s, connected +0.84 s, caller track
+1.40 s, audio +1.45 s. Devices were ready 0.74 s before Enter.

Run 6 timeline (Ben): accepted +0.04 s, connected +1.00 s, caller track +1.54 s, first
caller audio above the speech floor +2.24 s. The 0.7 s from track to audio is probably a
gap in the caller's speech (the probe needs RMS > 0.01), but the test counts it.

What moved the number:
- The agent meeting token is minted when the offer is sent (`PrefetchingMinter` in
  `queue_api.py`) and handed out on accept; a missed or declined offer drops it.
- While the card rings the portal loads the Pipecat/Daily SDK and starts the mic
  (`CallMedia.prepare`), so accept only connects.

What is left is Daily connect (~0.8 to 1.3 s) and the first remote track (~0.5 s). The
live test checks the median against 2 s and any single accept against 3 s; the host has
2 CPUs and other live suites run on it at the same time (run 3 timed out on load).

Other timings (run 5): offer to summary 0.16 to 0.26 s; wrap up view 0.38 s after End;
saved to available 0.14 s; call re-queued 3.16 s after Daily reported Ana gone.

## Screenshots

In `docs/screenshots/p3-5/`, each with a side by side against
`docs/design/in-call-focus.png` (`*-vs-design.png`, reference on the left):

1. `p3-5-1-ringing.png` incoming call card
2. `p3-5-2-in-call.png` Ana on the call, live transcript
3. `p3-5-3-ringing-after-drop.png` Ben's ring after QH-9
4. `p3-5-4-in-call-after-drop.png` Ben on the call
5. `p3-5-5-wrap-up.png` wrap up view after End
6. `p3-5-6-available.png` saved, available again

The build follows the Focus layout. DESIGN.md moved to Studio (ADR 0005) on Nov 16; per
Dutch, P3-5 is not restyled. P4-0 restyles every screen.

## Failures seen

- Run 1: accept to audio 4.4 s median (fixed above).
- Run 3: Playwright timeout under load (load average above 11 from another worker's live
  suite).
- Run 4: the caller talking over the requeue line interrupted it and the LLM replied
  instead. Requeue and goodbye lines are now uninterruptible (`hold._must_say`).

## Known gaps

- Daily takes 30 to 40 s to report a closed tab as left, so QH-9 starts then. A portal
  `pagehide` beacon could cut that; not in scope.
- AP-9 summary draft and suggested reason code are placeholders until Phase 4.
- QH-8 callback offer is not built.
- Accept to audio is borderline: median 1.77 s in run 5, 2.11 s in run 6. The rest is
  Daily connect (0.8 to 1.2 s) plus the first remote track. Going further would need the
  agent to join before accept (for example muted and hidden from the bot), which is a
  product decision.
- After the requeue line, the bot's gate is open again, so the caller's next words go to
  the intake LLM (run 6: "Sorry, I didn't catch that... A person will be with you
  shortly."). That fits "the bot speaks again", but the reply has no context about the
  requeue.
