# Spike: Flux voice controls on the read back lines (P5-8, PRD AP-22)

> Note (Nov 16, P5-10): demo IDs and names changed after this was written. Member IDs
> are now 6 digits (Maria Alvarez EVM100231 is now Maria Lopez 418302) and claim
> numbers 5 digits (CLM-900001 is now 70412); see docs/db.md. The results below are
> kept as measured, with the old IDs.

Date: 2026-11-16. Card t_1bd22bfd. Pipecat 1.15.0, `flux-alexis-en` on the Deepgram
`/v2/speak` websocket, Deepgram nova-3 prerecorded STT (smart_format on).

**Decision: the Settings speed slider runs from 0.8x to 1.2x (step 0.05), the range the
card proposed.** All five speeds tested (0.8, 0.9, 1.0, 1.1, 1.2) read every key entity
right, 50 of 50 each. Both expressivity extremes (-2 and 2, at speed 1.0) also read 50 of
50, so the Expressiveness help text has no warning. The slow read back speed stays at
0.9x by default (50 of 50).

## What runs

`bot/tests/test_tts_flux_controls_live.py` (`@pytest.mark.live`). It reuses the P5-5
harness in `bot/tests/harness/readback.py` (docs/spikes/tts-flux-vs-aura.md), adding
`run_flux_controls`, `speak_url(speed=, expressivity=)`, and `CONTROL_LINES`. Unit tests
are in `bot/tests/test_harness_flux_controls.py`.

```bash
READBACK_RUNS=5 READBACK_OUT=/tmp/flux-controls.json \
  uv run pytest -m live bot/tests/test_tts_flux_controls_live.py -s
```

Each setting connects with `speed` and `expressivity` on the URL, the same query
parameters `DeepgramFluxTTSService` sends at connect. Then, for each line and each of
the 5 runs:

1. Send `Speak` + `Flush` and time **time to first audio (TTFA)** from sending the text
   to the first audio chunk, on a socket that has already said `Connected`.
2. Transcribe the audio with nova-3 and score each key entity, exactly as in P5-5. A
   number only counts when it is in the transcript as that number.

Settings: speed 0.8, 0.9, 1.0, 1.1, 1.2 at expressivity 0; expressivity -2 and 2 at
speed 1.0.

## The lines

The six P5-5 lines (identity read back with the member ID in groups, summary read back
with DOB, claim number, and amount, 2:00 PM and 8 Oak Street, 7 business days, place in
line, and member ID and DOB as written), plus one new line:

| Line | Text | Checked |
| --- | --- | --- |
| verified | Thanks, you are verified. | "you are verified" or "you're verified" heard. A customer reported Flux garbling it at 1.0 and 1.1. |

## Results

5 runs per line per setting, 10 key entity checks per run (50 per setting). The
"verified" line is counted separately because it has no ID, date, or number in it.

| Setting | Key entities right | "you are verified" heard | TTFA median (s) |
| --- | --- | --- | --- |
| speed 0.8 | **50 / 50** | 4 / 5 | 0.153 |
| speed 0.9 | **50 / 50** | 5 / 5 | 0.153 |
| speed 1.0 | **50 / 50** | 4 / 5 | 0.144 |
| speed 1.1 | **50 / 50** | 5 / 5 | 0.123 |
| speed 1.2 | **50 / 50** | 4 / 5 | 0.148 |
| expressivity -2 | **50 / 50** | 5 / 5 | 0.154 |
| expressivity 2 | **50 / 50** | 5 / 5 | 0.145 |

The same table as the harness prints it (the verified line included):

| Voice | Entities right | TTFA median (s) | Misses |
| --- | --- | --- | --- |
| speed 0.8 | 54 / 55 (98%) | 0.153 | verified:verified x1 |
| speed 0.9 | 55 / 55 (100%) | 0.153 | none |
| speed 1.0 | 54 / 55 (98%) | 0.144 | verified:verified x1 |
| speed 1.1 | 55 / 55 (100%) | 0.123 | none |
| speed 1.2 | 54 / 55 (98%) | 0.148 | verified:verified x1 |
| expressivity -2 | 55 / 55 (100%) | 0.154 | none |
| expressivity 2 | 55 / 55 (100%) | 0.145 | none |

The three misses all heard **"Thanks. You were verified."** (0.8 run 2, 1.0 run 4,
1.2 run 0). That is "you are" said or heard as "you were", 3 times in 25 at speeds 0.8,
1.0, and 1.2. There was no garble at 1.1 (0 in 5). It has nothing to do with speed: it
happened at 1.0 as well as at the ends of the range. The bot never says this line. Its
verified line is the LLM's "your identity is verified", which played fine at 1.1 in the
live call below. Speed does not explain the customer's report. A wording without the
contraction risk ("Thanks, you're verified" or "your identity is verified") avoids it.

TTFA does not change with the settings: 0.12 to 0.15 s median, the same as P5-5
(0.118 to 0.143 s for Alexis).

### Speed takes effect

Median seconds of audio per line (5 runs each), and the median ratio to speed 1.0 over
the 7 lines:

| Setting | identity_readback | summary_readback | callback | appeal_timing | hold_update | raw_formats | verified | vs 1.0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| speed 0.8 | 11.68 | 20.72 | 7.92 | 5.44 | 5.84 | 15.44 | 1.60 | 1.26 |
| speed 0.9 | 10.64 | 19.04 | 5.92 | 5.60 | 5.28 | 12.56 | 1.60 | 1.14 |
| speed 1.0 | 9.52 | 15.60 | 6.00 | 4.48 | 4.64 | 10.48 | 1.52 | 1.00 |
| speed 1.1 | 7.44 | 13.36 | 5.36 | 4.00 | 4.72 | 9.76 | 1.60 | 0.89 |
| speed 1.2 | 8.08 | 12.48 | 5.20 | 3.76 | 6.40 | 8.32 | 1.76 | 0.85 |
| expressivity -2 | 9.68 | 15.04 | 5.44 | 4.08 | 5.36 | 9.20 | 1.52 | 0.96 |
| expressivity 2 | 9.20 | 15.36 | 6.40 | 4.56 | 5.36 | 10.24 | 1.68 | 1.02 |

The ratios follow the speed (about 1/speed). Flux's own pauses vary per take, which is
why some short lines (hold_update at 1.2) run longer.

## Live call (step 5)

`server/tests/test_flux_controls_live.py` sets Flux 1.1x, Warm (expressivity 1), and slow
read backs on in the Settings page, previews, saves, and places a browser call as Maria
Alvarez. Real output from the bot log, run on Nov 16:

```
[settings] session=5c24655b-... tts=flux/flux-alexis-en speed=1.1 expressivity=1 slow_readback=0.9 pack=claims_status read_back=on hold_every=60s stt_intake=nova-3-general stt_assist=nova-3-general
[timing] event=tts_speed t=1794853445.251 speed=0.9 reason=readback session=5c24655b-...
[timing] event=tts_speed t=1794853452.403 speed=1.1 reason=restore session=5c24655b-...
[timing] event=tts_speed t=1794853518.290 speed=0.9 reason=readback session=5c24655b-...
[timing] event=tts_speed t=1794853524.608 speed=1.1 reason=restore session=5c24655b-...
FluxTTSService#0: configuration applied: {'speed': 0.9}
FluxTTSService#0: configuration applied: {'speed': 1.1}
FluxTTSService#0: configuration applied: {'speed': 0.9}
FluxTTSService#0: configuration applied: {'speed': 1.1}
Flux TTS websocket connects: 1
```

Bot turns as the caller heard them, with the speed in effect when each one started:

| Speed | Length | Words/s | Heard |
| --- | --- | --- | --- |
| 1.1x | 9.1 s | 3.19 | hi thanks for calling you're speaking with deepgram vitals an ai assistant ... |
| **0.9x** | 10.4 s | **1.83** | thanks i have maria alvarez member id e v m one zero zero two three one is that right |
| 1.1x | 5.0 s | 2.58 | thank you maria your identity is verified what are you calling about today |
| 1.1x | 3.5 s | 3.39 | do you have the claim number it's on your explanation of benefits |
| 1.1x | 2.8 s | 3.55 | about when was the mri and who was the provider |
| 1.1x | 6.1 s | 3.29 | what is the problem with the claim for example is it denied still pending ... |
| **0.9x** | 8.8 s | **2.27** | maria alvarez you're calling because your mri claim was denied ... is that right |

Both read backs (identity, and the final summary) played slower, and each next line was
back at 1.1x. There was one Flux connection for the whole call, and Flux confirmed every
speed change. Questions that only ask for a detail stayed at 1.1x.

A second run (session 4dabb9a3, after a CSS change for the screenshots) showed the same
pattern. The identity read back was 2.24 words/s at 0.9x and the summary read back 2.21
words/s at 0.9x. The questions were 3.60 and 3.65 words/s at 1.1x. There was one
connection and 4 of 4 speed changes applied.

One fix came out of the live call. The first version also treated a line as a read back
when it only had the words "member ID", "date of birth", "claim number", or "born". On
the first live run, the questions "What's your date of birth and member ID?" and "Do you
have the claim number?" were slowed too. Read backs are now spotted by the value itself:
an ID or claim number written or spelled out, or a date. The questions are unit tests in
`bot/tests/test_tts_pacing.py`. The second run (above) left them at 1.1x.

## Not tested

- Language and model combinations that answer `SPEED_NOT_SUPPORTED`: only
  `flux-alexis-en` was run here. Dutch's /v2/speak checks covered speed 1.15 and
  expressivity -1 and 2. If another voice refuses a speed, the bot logs Flux's
  ConfigureFailure and keeps speaking at the speed it had.
- Pronunciation markup: none is used. Flux rejects it at any speed but 1.0
  (`DATA-0002`).
