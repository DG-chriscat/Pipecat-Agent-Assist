# Spike: Flux TTS vs Aura-2 on the read back lines (P5-5, PRD IN-11)

Date: 2026-11-16. Card t_665a04da. Pipecat 1.15.0, Deepgram nova-3 prerecorded STT
(smart_format on), Deepgram `/v1/speak` (Aura-2) and `/v2/speak` (Flux) websockets.

**Decision: Flux is the default voice, `flux-alexis-en`.** Flux Alexis read every key
entity right in 80 of 80 checks, Aura-2 Thalia 79 of 80. IN-11 says Flux becomes the
default when it reads member IDs, dates, amounts, and claim numbers at least as well as
Aura-2; it did. Aura-2 stays selectable on the Settings page. The second Flux voice we
tried, `flux-hannah-en`, was worse (74 of 80) and is not the default.

## What runs

`bot/tests/test_tts_readback_live.py` (`@pytest.mark.live`), scoring code in
`bot/tests/harness/readback.py` (unit tests: `bot/tests/test_harness_readback.py`).

```bash
READBACK_RUNS=5 uv run pytest -m live bot/tests/test_tts_readback_live.py -s
```

For each voice and each line, `RUNS` times:

1. Open the speak websocket the bot uses (`/v1/speak` for Aura-2, `/v2/speak` for Flux,
   linear16, 16 kHz), send `Speak` + `Flush`, and time **time to first audio (TTFA)**
   from sending the text to the first audio chunk, on an already open socket (as in a
   call). For Flux the clock starts after its `Connected` message.
2. Transcribe the audio with Deepgram nova-3 prerecorded, `smart_format=true`.
3. Score each key entity: right only if it is in the transcript exactly (after turning
   number words into digits). The same STT hears every voice, so STT mistakes cost each
   voice the same.

## The lines

The identity read back and the hold update are the bot's real lines
(`intake.READBACK_LINE` with `speak_member_id`, `hold.hold_line`). The others are the
kind of line the LLM says in the read back node, written as an LLM writes them.

| Line | Text | Entities |
| --- | --- | --- |
| identity_readback | Thanks. I have Maria Alvarez, member ID E V M, one zero zero, two three one. Is that right? | member ID EVM100231 in groups |
| summary_readback | So that's Maria Alvarez, date of birth March 12, 1984, calling about claim CLM-900001 for $1,240, which was denied. Is that right? | DOB, claim number, amount |
| callback | A person will call you back at 2:00 PM today, and the corrected bill goes to 8 Oak Street. | time, single digit street number |
| appeal_timing | Once the appeal is filed, a decision usually takes 7 business days. | 7 business days |
| hold_update | Thanks for waiting. You're number 2 in line. A person will be with you shortly. | place in line |
| raw_formats | Your member ID is EVM100231 and your date of birth is 03/12/1984. | member ID as written, DOB as digits |

## Results

Two live runs, 3 and 5 runs per line (8 per line per voice). 10 entity checks per run.

| Voice | Entities right | TTFA median, run 1 (s) | TTFA median, run 2 (s) | Misses |
| --- | --- | --- | --- | --- |
| aura-2-thalia-en | 79 / 80 (98.8%) | 0.105 | 0.106 | hold_update: "number two in **mind**" x1 |
| **flux-alexis-en** | **80 / 80 (100%)** | 0.143 | 0.118 | none |
| flux-hannah-en | 74 / 80 (92.5%) | 0.118 | 0.153 | summary_readback: amount x3 ("$12.40"), claim number x3 |

TTFA per line (median of the runs, seconds):

| Voice | identity_readback | summary_readback | callback | appeal_timing | hold_update | raw_formats |
| --- | --- | --- | --- | --- | --- | --- |
| aura-2-thalia-en, run 1 | 0.112 | 0.104 | 0.081 | 0.106 | 0.078 | 0.106 |
| aura-2-thalia-en, run 2 | 0.107 | 0.105 | 0.100 | 0.115 | 0.114 | 0.106 |
| flux-alexis-en, run 1 | 0.120 | 0.114 | 0.167 | 0.122 | 0.174 | 0.158 |
| flux-alexis-en, run 2 | 0.116 | 0.120 | 0.115 | 0.117 | 0.119 | 0.122 |
| flux-hannah-en, run 1 | 0.111 | 0.150 | 0.118 | 0.154 | 0.118 | 0.110 |
| flux-hannah-en, run 2 | 0.173 | 0.146 | 0.152 | 0.112 | 0.118 | 0.191 |

Flux's first audio is 10 to 40 ms later than Aura-2's. That does not change the
greeting target: in the live Flux call below, the greeting's first audio reached Daily
0.27 s after the bot joined.

Run 1 was first scored with an earlier matcher that did not accept nova-3's
"CLM dash 900001" for CLM-900001 (it marked all three voices wrong on every summary
line). The matcher was fixed (a spoken "dash" is the hyphen; unit tested) and run 1 was
rescored from the saved transcripts; the table shows the rescored numbers.

## Formatting misses

- **Flux Hannah, `$1,240`.** 3 of 8 times smart_format wrote "$12.40 dollars"; with
  smart_format off the same audio reads "twelve forty dollars" in places. Hannah
  sometimes says the amount as "twelve forty", which a caller hears as $12.40. A
  reading of amounts as pairs is the same family of issue Chris filed for Epic in
  #product-tts. Alexis and Thalia said "one thousand two hundred and forty dollars"
  every time.
- **Flux Hannah, CLM-900001.** 3 of 8 times the transcript had an extra zero
  ("CLM dash 9000001") or lost digits ("CLM-nine and 1"). Re-transcribing 4 new Hannah
  takes with smart_format off: 1 of 4 had "nine zero zero zero zero **zero** one", so
  this one is the voice (it drew out the run of zeros), not only STT.
- **Aura-2 Thalia, hold line.** Once "number two in **mind**" for "in line". One miss in
  8; Flux voices had none on this line.
- **Both models, `03/12/1984`.** Written as digits, every voice reads "zero three twelve
  nineteen eighty four" (Aura-2 and Flux alike). A caller can follow it, but "March 12,
  1984" reads more naturally; the bot already never reads a DOB back (identity read
  back), and the read back prompt keeps dates out of the LLM's summary.
- **Both models, member ID as written (`EVM100231`).** Flux spells "E V M one zero zero
  two three one"; Aura-2 says "EVM" as a word once in a while, then the digits. The
  bot's own read back groups the ID (`speak_member_id`), so neither matters there.

## What changed because of this

- `agent_assist_bot.call_settings.DEFAULT_TTS_MODEL = "flux"`, default voice
  `flux-alexis-en` (and the same in `agent_assist_server.bot_settings`; a unit test keeps
  the two equal).
- Aura-2 Thalia remains one click away on the Settings page.

## Found on the way

- Flux closes an idle websocket after 60 s (`NET-0004`). Pipecat's websocket keeps it
  alive with pings: a pipeline idle for 75 s then spoke again with 0.11 s TTFA. The one
  `NET-0004` seen during the live call test came with Daily transport failures and
  Deepgram STT 1011 timeouts at the same moment, on a host at load 8 (another worker's
  build); it did not repeat on a quiet host.
- A caller who talks over a fixed line (greeting, read back) could freeze the flow: see
  the P5-5 PR, `intake.uninterruptible_tts_say`. Not Flux specific (Flows' own
  `tts_say` end marker was interruptible), but Flux's longer pauses between phrases
  (about 1.1 s inside the read back vs 0.45 s on Aura-2) made the scripted caller answer
  mid line and hit it.
