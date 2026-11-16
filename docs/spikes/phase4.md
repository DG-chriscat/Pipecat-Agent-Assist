# Phase 4 exit: each pack's trigger shows its card (P4-6)

> Note (Nov 16, P5-10): demo IDs and names changed after this was written. Member IDs
> are now 6 digits (Maria Alvarez EVM100231 is now Maria Lopez 418302) and claim
> numbers 5 digits (CLM-900001 is now 70412); see docs/db.md. The results below are
> kept as measured, with the old IDs; the P5-10 section at the end measures the new ones.

Date: 2026-11-16. Card t_ac945472. Pipecat 1.15.0, Deepgram nova-3 STT and Aura 2 TTS,
Anthropic (claude-haiku-5-5), Daily `dg-chriscat`, Supabase Postgres (a throwaway
schema per call, migrated and seeded, dropped after), headless Chromium 1600x1000.
Build host: 1 CPU.

PLAN.md exit: "each pack's trigger shows its card on a live call". PRD DC-6: "A card
shows on screen within 3 seconds of the words that triggered it." AP-8: "The agent can
type a question to the assist, such as 'Show the last 3 claims.'"

**Result: pass.** All three packs showed their card in the portal 0.88 to 1.61 s after
the trigger word (DC-6 target 3 s). The Ask bar answered "Show the last 3 claims" with a
Claims card and its source 0.33 s after Enter.

## What runs

`server/tests/test_phase4_exit_live.py` (`@pytest.mark.live`):

```bash
cd portal && npm run build && cd ..
PLAYWRIGHT_BROWSERS_PATH=... P4_SCREENSHOT_DIR=docs/screenshots/p4-6 \
  uv run --with playwright pytest -m live server/tests/test_phase4_exit_live.py -s -v
```

Needs `DAILY_API_KEY`, `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`.

Three scripted calls, one per pack. Each runs the real server under uvicorn, which
starts the bot on `POST /calls/browser`:

1. A daily-python caller (S1 harness, Deepgram Aura TTS) answers intake as the persona.
   The bot picks no pack by hand: `INTAKE_PACK` is unset, intake runs the default pack,
   and on accept the assist picks the pack from the intake answers (docs/assist.md
   "Pack at accept").
2. Agent Ana's portal (headless Chromium) goes available and accepts with Enter. The
   test checks the call plan tile is up and the caller panel loaded its history.
3. The caller says the trigger line. A `MutationObserver` in the page stamps
   `Date.now()` when the expected card is first on screen. The trigger word's end in
   the played audio comes from Deepgram prerecorded word timings on the exact PCM
   played. Both clocks are this host's wall clock.
4. **DC-6** passes when card on screen minus trigger word end is under 3 s. The test
   also checks the card id, that `assist_events` logged it under the expected pack, and
   records the shelf and the words marked in the conversation.
5. On the claims call the agent presses Cmd+K, types the question, and presses Enter,
   twice: "Show the last 3 claims", then "Any denied claims?". Each answer must be a
   new shelf tile, fully in view, with "You asked ...", the source "Claims system", and
   the expected claim ids.

| Call | Persona | Pack | Caller says | Expected card |
| --- | --- | --- | --- | --- |
| claims | Maria Alvarez, EVM100231 | `claims_status` | "So why did my MRI claim get denied?" | hero `nbs:retro_auth_review:CLM-900001` |
| payments | James Chen, EVM100232 | `account_payments` | "I looked at my bank account and I was charged twice for my premium." | hero `nbs:duplicate_premium_refund:PAY-000064` |
| general | Linda Nguyen, EVM100234, asking a general question | `general_question` | "So what is an explanation of benefits, exactly?" | article tile `kb:KB-CL-010` |

Linda's intake answers in the test are a general question ("I got a paper in the mail
from you, and I have a general question about what it means."), not her CO-242 denial,
so the call is a general question and lands on the fallback pack.

## Timings (run 9, the recorded run)

Trigger word to card on screen is the DC-6 figure. Utterance end to card is from the
end of the whole trigger line. Bot line to send is the bot's own `latency_ms` on the
`card_sent` row (final transcript line timestamp to the app message being sent).

| Call | Pack | Card on screen | Trigger word | Trigger word to card (s) | Utterance end to card (s) | Bot line to send (ms) | DC-6 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claims | claims_status | `nbs:retro_auth_review:CLM-900001` | denied | **0.88** | 0.68 | 366 | pass |
| payments | account_payments | `nbs:duplicate_premium_refund:PAY-000064` | twice | **1.61** | 0.64 | 397 | pass |
| general | general_question | `kb:KB-CL-010` | benefits | **1.38** | 0.66 | 393 | pass |

Ask bar (claims call, run 9):

| Question | Card | Enter to card on screen (s) | Big answer | Records (source "Claims system") | Bot ask to send (ms) |
| --- | --- | --- | --- | --- | --- |
| Show the last 3 claims | `ask:ask-mwj4iwgm-1` | **0.33** | 2 claims | CLM-900001, CLM-900002 | 37 |
| Any denied claims? | `ask:ask-mwj4j1ia-2` | **0.27** | $1,850 | CLM-900001 | 36 |

Maria has two claims in the seed, so "the last 3" shows both.

Supporting cards each call put on the shelf (run 9), and the words marked in the
conversation:

| Call | Shelf | Marked |
| --- | --- | --- |
| claims | `card:denied_claim`, call plan (then the two Ask tiles) | "denied" |
| payments | `card:payment_history`, call plan, `alert:duplicate_charge:PAY-000064` | "charged twice" |
| general | `kb:KB-CL-010`, call plan | "explanation of benefits" |

### All runs

| Run | Build | claims (s) | payments (s) | general (s) | Ask (s) | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | first cut | intake ended `emergency` | 1.85 | test error (trigger word lookup) | - | 1 of 3 |
| 2 | claims, general only | trigger audio misheard | - | 1.30 | - | 1 of 2 |
| 3 | | 0.94 | missed the offer | 1.36 | 0.21 | 2 of 3 |
| 5 | | 0.99 | 1.61 | 1.27 | 0.22 | 3 of 3 |
| 7 | | 0.92 | no card | 1.55 | 0.22, 0.25 | 2 of 3 |
| 8 | split phrase fix | 0.79 | 1.50 | 1.26 | 0.27, 0.23 | 3 of 3 |
| 9 | caller history fix in the test | **0.88** | **1.61** | **1.38** | **0.33, 0.27** | **3 of 3** |

Run 4 was stopped by hand (the old build) and run 6 was claims only, to check the Ask
tile scrolls into view. No card was ever over 3 s once it showed: the slowest was 1.85 s.

## What the failing runs found

- **Run 1, claims:** intake STT heard "Maria Halperaz" and the intake LLM called
  `report_emergency`, so the call queued unverified and the test stopped. Intake
  identity capture is the side card in PLAN.md, not this card; every later Maria call
  (runs 3 to 9) verified.
- **Runs 1 and 2, trigger audio:** Aura TTS output varies run to run. In run 1 the
  general call's trigger word lookup failed on the transcript of the synthesized line;
  in run 2 Deepgram heard "...claim hit tonight" instead of "...claim get denied". The
  test now re-synthesizes the trigger line until the trigger word is in it (up to 4
  tries) so it measures the assist, not TTS.
- **Run 3, payments:** keyboard focus was still on the Available switch, so Enter did
  not reach the incoming call card and the offer timed out as missed. The test now
  clears focus before Enter (as `test_assist_ui_live.py` does).
- **Run 7, payments: a real bug, fixed.** Live STT ended a final line inside the
  trigger phrase: "and I was charged" / "twice for my premium." Neither line held
  "charged twice", so the refund rule never fired (only the pack's "premium" alert did).
  The engine now matches a phrase that starts in the same speaker's previous final line
  (at most 3 s earlier) and ends in this one; the trigger is the part in this line.
  Unit tests: `test_trigger_phrase_split_across_two_final_lines_still_fires`,
  `test_split_phrase_needs_the_same_speaker_and_a_short_gap`. Runs 8 and 9 hit the same
  split ("I looked at my bank account," / "and I was charged twice ...") and passed.
- **Run 8, caller panel:** after the first call, "Caller history is unavailable" showed
  on the next calls. The server caches its caller history repo (and its schema) on
  `app.state`; the test reuses one app across three schemas and did not clear it. Test
  only: it now clears `caller_history` between calls and asserts the panel loaded.

## Card latency inside the bot

The bot's `latency_ms` (final transcript line to send) was 366 to 397 ms for the hero on
all three packs: one connector search (Supabase round trip from this host, ~350 ms on a
cache miss) and the send. The rest of the 0.9 to 1.6 s is STT endpointing (the final
line arrives after the caller stops), the Daily app message, and React rendering.
Supporting cards from the LLM pass (KB article cards; in run 9 on the claims call
only) arrive about 3.3 to 3.7 s after the line. They are not the trigger's card, so
DC-6 is measured on the hero and the KB answer; the LLM pass is debounced and off the
hot path by design (docs/assist.md).

## Screenshots

In `docs/screenshots/p4-6/`, from run 9, each in light and dark:

1. `p4-6-claims-card-{light,dark}.png`: Maria, retro authorization hero, denied claim
   record, call plan; "denied" marked.
2. `p4-6-ask-1-claims-{light,dark}.png`: after "Show the last 3 claims": the Claims
   answer tile ("2 claims", "You asked ...", Claims system) on the shelf.
3. `p4-6-ask-2-claims-{light,dark}.png`: after "Any denied claims?": the second answer
   ($1,850, CLM-900001), scrolled into view as the fifth tile.
4. `p4-6-payments-card-{light,dark}.png`: James, refund hero for PAY-000064, payment
   history, call plan, duplicate premium alert; "charged twice" marked.
5. `p4-6-general-card-{light,dark}.png`: Linda, general question pack, KB-CL-010
   "Reading an Explanation of Benefits (EOB)" article, call plan; "explanation of
   benefits" marked. Hero stays "Listening" (the general question pack has no next
   best step rules).

## Offline coverage (CI)

`bot/tests/test_assist_ask.py` (Ask parsing, contract, engine, fallback pack KB answer),
`test_assist.py` (split phrase), `test_assist_runtime.py` (Ask from the agent only, as
its own task), portal `state/assist.test.ts`, `components/AssistColumn.test.tsx`
(Cmd+K, Enter, Esc, status line, answer tile, scroll into view), `hooks/useCall.test.ts`
(`assist_ask` client message).

## P5-10 rerun with the new IDs (Nov 16)

`server/tests/test_phase4_exit_live.py` after the P5-10 rekey (6 digit member IDs,
5 digit claim numbers, easy names), Settings defaults: **3 passed**.

| Call | Persona | Card | Trigger word end to card | Utterance end to card |
| --- | --- | --- | --- | --- |
| claims | Maria Lopez | `nbs:retro_auth_review:70412` | 0.737 s | 0.572 s |
| payments | James Carter | `nbs:duplicate_premium_refund:PAY-000067` (plus the duplicate charge alert) | 1.594 s | 0.626 s |
| general | Linda Nguyen | `kb:KB-CL-010` | 1.301 s | 0.580 s |

Ask on the claims call: "Show the last 3 claims" answered in 0.223 s (2 claims: 70412,
70358); "Any denied claims?" in 0.416 s ($1,850, 70412). All cards within 3 s (DC-6).
