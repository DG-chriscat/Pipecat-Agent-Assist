# Flux STT for intake, Nova-3 for assist (card t_0cbf3922, P5-7, PRD IN-14)

> Note (Nov 16, P5-10): demo IDs and names changed after this was written. Member IDs
> are now 6 digits (Maria Alvarez EVM100231 is now Maria Lopez 418302) and claim
> numbers 5 digits (CLM-900001 is now 70412); see docs/db.md. The results below are
> kept as measured, with the old IDs.

Result in one line: both sides are selectable in Settings. Flux intake answers sooner
(0.27 s median, 0.71 s p90) but missed Linda Nguyen's last name on 5 of 5 runs
(identity 8 of 10 vs 10 of 10 on Nova-3), so **Nova-3 stays the default on both
sides**. Assist latency, labels, and the card timing are fine on every model; Nova-3
general stays the assist default.

Date: 2026-11-16. Pipecat 1.15.0, Deepgram `flux-general-en` (/v2/listen) and
`nova-3-general` / `nova-3-medical` (/v1/listen), Flux TTS Alexis, Anthropic (default
`create_llm` model), Daily `dg-chriscat`, Supabase Postgres (throwaway schema per run).
Host: 2 CPU, shared with other workers' test runs (load average is noted per batch).

## What changed

STT model per side, a Settings value each (Voice section, "Listening" group):

| Side | Setting | Default | Choices |
| --- | --- | --- | --- |
| Intake (voice bot) | `stt_intake_model` | `nova-3-general` (see Decision) | `nova-3-general`, `flux-general-en` |
| Live assist (listen only, per person track) | `stt_assist_model` | `nova-3-general` | `nova-3-general`, `nova-3-medical`, `flux-general-en` |

There is no Flux medical model, so Nova-3 Medical is offered on the assist side only.
Built by `bot/agent_assist_bot/stt.py`.

### Intake with Flux

- `DeepgramFluxSTTService` (`ChunkedFluxSTTService`: sends 80 ms of audio per message,
  as the Flux docs recommend; Daily delivers 20 ms frames), pack keyterms, `numerals`
  on for the whole call.
- Flux owns turn detection. The intake has no Silero VAD and no smart turn: the user
  aggregator runs `ExternalUserTurnStrategies(enable_interruptions=True)`, so Flux's
  StartOfTurn interrupts the bot (barge in still works) and its EndOfTurn ends the
  caller's turn. With Nova-3 the bot keeps Silero VAD plus smart turn, as before.
- Identity step: `eot_threshold` 0.7 to 0.85 and `eot_timeout_ms` 5000 to 7000 while the
  identity and identity_confirm nodes are active, back to 0.7 / 5000 after the check,
  sent mid stream as a Flux `Configure` message (Pipecat's settings update; no
  reconnect). Seen in the bot log on every run: `Configure accepted` (ConfigureSuccess)
  both ways. This replaces the Nova-3 tweak (endpointing 800 ms, VAD stop 0.8 s,
  smart_format), which still applies when Nova-3 is picked.
- Flux has no `smart_format`. `numerals` covers what identity needs: the member ID
  comes through as digits ("E V M 1 0 0 2 3 1") and the DOB as "March 12th 1984".
- Eager end of turn: **off**. Flux can answer a predicted end of turn early, but each
  prediction costs an LLM call, including the ones Flux withdraws (Deepgram puts it at
  50 to 70 percent more LLM calls). The intake runs function calls on most turns, so a
  withdrawn prediction would also risk a duplicate tool call. Not measured here; a
  candidate for later if the response time needs it.

### Assist with Flux (listen only)

- `should_interrupt=False`, no VAD. Flux's Update events become interim lines and
  EndOfTurn the final line, so the portal gets both, as with Nova-3.
- Flux proposes turns (`ProposedUserStarted/StoppedSpeakingFrame`) and recommends its
  turn strategies in an `STTMetadataFrame`. `TranscriptRelay` now keeps those inside the
  listen branch, so the agent talking can never barge in on the intake bot and a Flux
  listen branch cannot switch a Nova-3 intake to Flux turns.
- An idle Flux stream stays open: a probe with no audio for 400 s was still connected,
  so the listen branches need no keepalive during intake and hold.
- Labels are unchanged: a line's role is the track it was heard on.

## Assist side

`bot/tests/test_live_s2.py` (the S2 listen only harness, two synthetic voices in a Daily
room, `S2_STT_MODEL` per run) and `server/tests/test_assist_live.py` (the full call:
intake, accept, trigger line, card on the agent's client; `ASSIST_STT_MODEL`). One run
each, load average about 1.

| | Nova-3 general | Flux | Nova-3 Medical |
| --- | --- | --- | --- |
| S2 script: end of speech to final line, median | **0.41 s** (n=9) | 0.59 s (n=9) | |
| S2 script: speaker labels correct | 16 of 16 | 14 of 14 | |
| Claims script: end of speech to final line, median | 0.37 s (n=6) | | 0.44 s (n=6) |
| Claims script: speaker labels correct | 16 of 16 | | 16 of 16 |
| Claims script: medical terms heard | 16 of 18 | | 16 of 18 |
| Full call: trigger word to card on the agent client | 0.78 s | 0.88 s | |
| Full call: test passed (hero, labels, caller gets no assist) | yes | yes | |

- Nova-3 general matches the S2 baseline (0.44 s median). Flux is slower here (0.59 s);
  likely because a listen only Flux line ends on Flux's end of turn model instead of
  Deepgram endpointing (not isolated). Still well inside the 3 s card budget (DC-6), so
  it is a valid choice, not the default.
- Medical terms: 18 terms in the claims script (MRI, lumbar, radiculopathy, epidural,
  steroid, gadolinium, contrast, herniated, L4, L5, stenosis, neurologist, gabapentin,
  methylprednisolone, milligrams, physical therapy, sciatica, electromyography). Both models
  heard all but L4 and L5, and both heard those as "l four dash l five": right words,
  not written as codes, so the score counts them as misses. On this script Nova-3
  Medical heard nothing Nova-3 general missed, and was a little slower. One run, so it
  is no evidence against Nova-3 Medical on harder vocabulary; it stays selectable.
- The Daily `Mediasoup ... ConsumerNoLongerExists` errors in two outputs come at room
  teardown, after the measurements.

## Identity harness: Flux intake vs Nova-3 intake

`bot/tests/test_live_intake.py`, 5 personas x 2 runs per model, same day, same host,
one batch after the other. `INTAKE_STT_MODEL` picks the intake model the way a cold bot
gets it from Settings. Everything else is the same (Flux TTS Alexis, read back on,
identity v2 from #50: close match counts as correct, Daniel's wrong DOB must be
not_verified). Load average 1 to 4 (another worker's suite ran during the first Flux
runs). The Nova-3 batch is this card's baseline: `docs/spikes/identity.md` v2 ran on a
different day and branch (7 of 10, two misses from the emergency misfire fixed since),
so a same day rerun is the fair comparison.

New harness field: **response gap**, from the end of the caller's voice in each line
(playback end minus its trailing silence) to the bot's first audio after it. It covers
turn detection, the final transcript, the LLM, and the TTS, so it is what the caller
feels. LLM and TTS time to first byte were the same on both models (bot logs: LLM median
0.52 s Flux runs, 0.49 s Nova-3 runs; TTS 0.16 s and 0.18 s), so the difference between
the two is turn detection.

### Flux intake (`flux-general-en`)

| persona | run | result | correct | member ID heard | intake s | gap median s | heard name |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Maria | 1 | verified | yes | EVM100231 | 110.9 | 3.38 | Maria Alvarez |
| Maria | 2 | verified | yes | EVM100231 | 105.6 | 3.16 | Maria Alvarez |
| James | 1 | verified | yes | EVM100232 | 100.1 | 3.06 | James Chen |
| James | 2 | verified | yes | EVM100232 | 89.4 | 2.44 | James Chen |
| Aisha | 1 | verified | yes | EVM100233 | 112.0 | 2.77 | Aisha Patel |
| Aisha | 2 | verified | yes | EVM100233 | 104.2 | 3.38 | Aisha Patel |
| Linda | 1 | not_verified | **no** | EVM100234 | 92.2 | 2.38 | Linda Hillenda |
| Linda | 2 | not_verified | **no** | EVM100234 | 114.8 | 3.29 | Linda Wayne |
| Daniel | 1 | not_verified | yes | EVM100235 | 108.4 | 2.46 | Daniel Okafor |
| Daniel | 2 | not_verified | yes | EVM100235 | 174.5 | 2.98 | Daniel Okafor |

### Nova-3 intake (`nova-3-general`, baseline)

| persona | run | result | correct | member ID heard | intake s | gap median s | heard name |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Maria | 1 | verified | yes | EVM100231 | 103.4 | 2.95 | Maria Alvarez |
| Maria | 2 | verified | yes | EVM100231 | 96.6 | 6.01 | Maria Alvarez |
| James | 1 | verified | yes | EVM100232 | 106.7 | 4.50 | James Chen |
| James | 2 | verified | yes | EVM100232 | 111.8 | 3.87 | James Chen |
| Aisha | 1 | verified | yes | EVM100233 | 103.1 | 3.14 | Aisha Patel |
| Aisha | 2 | close_match | yes | EVM100233 | 98.8 | 3.80 | Ayesha Patel |
| Linda | 1 | verified | yes | EVM100234 | 103.9 | 2.75 | Linda Nguyen |
| Linda | 2 | verified | yes | EVM100234 | 136.7 | 2.78 | Linda Nguyen |
| Daniel | 1 | not_verified | yes | EVM100235 | 93.1 | 2.54 | Daniel Okafor |
| Daniel | 2 | not_verified | yes | EVM100235 | 112.3 | 2.83 | Daniel Okafor |

### Side by side

| | Flux intake | Nova-3 intake |
| --- | --- | --- |
| Identity correct | **8 of 10** | **10 of 10** |
| Member ID heard right | 10 of 10 | 10 of 10 |
| Intake under 120 s | 9 of 10 | 9 of 10 |
| Intake seconds, median | 107.0 | 103.7 |
| Caller stop to bot first audio, median | **2.90 s** (n=76) | 3.17 s (n=72) |
| Caller stop to bot first audio, p90 | **3.82 s** | 4.53 s |

Linda Nguyen with Flux, 3 more runs to check the two misses were not chance: 0 of 3
(heard "Linda Winé", "Linda Ryan", "Linda Nickiem"; member ID right every time; gap
median 2.65 s). **Flux missed Linda's last name in 5 of 5 runs; Nova-3 got it in 2 of 2.**

What the logs show:

- The miss is the last name, not the turn taking. Flux heard "Linda Newen", "Linden
  Wynn", "Linden England" on the first identity line, then the one correction ("No, my
  name is Linda, L I N D A, Nguyen") came back as "Linda, hille n d a" or "Linda, l i n
  d a, and Ryan". Identity v2 allows one fix, so the call went to the agent as
  not_verified (the agent still gets the verify hero, IN-13). Nova-3 also misheard
  "London Nguyen" once, but got "Nguyen" on the correction. The harness spells only the
  first name, so a misheard last name is never spelled.
- No mid line turn splits with Flux: with the identity thresholds on, every identity
  line came through as one turn ("... my member ID is EVM 1 0 0 2 3 4."). Nova-3 split
  the same line into 2 or 3 transcripts in 7 of 10 calls ("My date of birth is
  07/04/1979. In" / "my member ID is EVM 1 0 0 2 3 2."), which smart turn mostly held
  together.
- The Configure messages were accepted every time (20 ConfigureSuccess in the Flux
  batch: on and off once per call).
- Faster: the bot answered 0.27 s sooner at the median and 0.71 s sooner at p90 with
  Flux. Most of the Nova-3 tail is one call (Maria run 2: gaps of 6.1, 8.0, and 6.0 s
  on its last three lines; cause not traced).

### Decision: Nova-3 stays the intake default

The card's rule: keep Flux intake the default unless it regresses identity; if it does,
keep Nova-3 and say why. It did: 8 of 10 vs 10 of 10, and 0 of 5 on the hard last name,
which is the IN-6 check the demo relies on. So:

- `stt_intake_model` default is `nova-3-general`; Flux is one click away in Settings.
- `stt_assist_model` default is `nova-3-general` (no change).

What would make Flux the intake default (follow up, not done here): pass the caller's
spelled letters to the identity check as well as the heard name, or ask for the last
name spelled in the identity question. Both work on the name problem itself, which also
helps Nova-3. Flux keyterms cannot carry member names (the caller is not known before
the check, and the list is fixed per pack).
