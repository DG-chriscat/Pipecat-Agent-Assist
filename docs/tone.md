# Caller tone, mood trend, tone tip (P4-5)

PRD IN-10 (a tone reading per caller turn, saved with the transcript) and AP-14 (mood
trend for the whole call, tone tip card when mood drops two steps). PRD calls this
"Pipecat Tone" / "RCS perception".

## What reads the tone

Pipecat 1.15's `PipecatToneReader` (`pipecat.processors.tone.reader`, part of
expressive conversation, **alpha**). Model `pipecat-ai/tone-models`, revision
`reader-rc2`, fp16 weights, ONNX on the CPU, one thread per reader.

**It reads the words, not the audio.** Each final caller transcript line (with the
line before it as context) gets the two likeliest emotions with confidences and a
sarcasm probability. The emotions: neutral, happy, amused, angry, sad, afraid,
surprised, disgusted, contemptuous. Every place the mood shows in the portal carries
the tooltip "Tone reader, words only."

## Where it sits

```
intake branch:  AudioRoute(intake) -> Deepgram STT -> CallerToneReader[intake] -> IntakeToneTap -> ...
caller listen:  AudioRoute(caller) -> VAD -> Deepgram -> CallerToneReader[caller] -> TranscriptRelay(caller)
agent listen:   AudioRoute(agent)  -> VAD -> Deepgram -> TranscriptRelay(agent)          (no reader)
```

- Both readers share one loaded model (Pipecat loads it once per process).
- The intake reader takes the bot's previous reply as context itself
  (`LLMContextAssistantTurnFrame`). The caller listen reader gets the agent's last
  final line (`bot.PreviousLine`).
- `IntakeToneTap` saves each intake caller line with its reading
  (`transcript_lines`, line ids `intake-caller-n`) and adds the reading to the call's
  `MoodTrend`. After accept, `TranscriptRelay` attaches the reading to the final caller
  line (`data["tone"]`), which the recorder saves and the assist reads.
- RTVI `user-input` messages are turned off (`user_input_enabled=False`): they would
  carry the intake reading to everyone in the room, the caller included.

## Mood steps

`bot/agent_assist_bot/tone.py` maps each reading to a five step scale:

| step | mood | from |
| --- | --- | --- |
| 0 | Pleased | happy, amused |
| 1 | Calm | neutral, surprised, unknown labels |
| 2 | Uneasy | a negative emotion under 0.5 confidence, or sarcasm >= 0.5 |
| 3 | Worried | sad, afraid |
| 4 | Frustrated | angry, disgusted, contemptuous |

A positive label under 0.5 confidence counts as Calm. `score` = step / 4 (0 calm to 1
upset) is the bar height in the portal; Uneasy and up draw in bakelite.

**Trend label**: current mood plus the direction against the mean of the two
readings before it (`worsening` at +0.5 or more, `easing` at -0.5 or less, else
`steady`), e.g. "Worried, easing". Chip tone: ok (Pleased, Calm), warn (Uneasy,
Worried), bad (Frustrated).

**Two step drop (tone tip)**: a reading two or more steps above the calmest reading
since the last tip (Calm at call start) offers a tone tip card, then the baseline
re-arms at that reading. Intake readings count toward the baseline but never tip (no
agent yet). The tip goes through the P4-3 `_offer` path: same rate limit (DC-7), rank
4 (above alerts, below every next step), never the hero, a newer tip replaces the
older one, `card_sent` is logged in `assist_events`.

| mood | tip headline |
| --- | --- |
| Uneasy | Check in |
| Worried | Reassure first |
| Frustrated | Acknowledge, then act |

## Saved with the transcript (IN-10)

`transcript_lines.tone` (from 0001) is the reader's emotion label; `tone_reading`
(jsonb, migration `0006_tone_readings.sql`) is the whole reading: `emotion`,
`confidence`, `labels` (top two), `sarcasm`, `step`, `mood`, `score`. Agent lines and
unread lines have both null. No new table; RLS from 0002 already covers
`transcript_lines`.

## Portal

- `assist` message `mood` (docs/assist.md) carries the trend; `state/mood.ts` turns
  it into bars and the chip.
- Caller panel Mood: label chip plus one bar per caller turn (`CallerPanel.tsx`).
- Call island: `data-testid="mood-chip"` shows the label in its tone, "Mood –" before
  the first reading.
- Shelf: `tone_tip` tile with the bakelite swatch, the headline as the one big answer,
  the why as its context line, source "Pipecat Tone reader, words only".

## Switch and model download

- `TONE_READER=0` (or `off`, `false`, `no`) builds the bot without readers; the
  transcript, assist, and portal work as before and the mood stays "Mood –".
- The model loads while the pipeline starts. A fresh container would download it then
  (about 3 s here) and hold the call start, so the image downloads it at build time:

  ```
  RUN python -m agent_assist_bot.tone_prefetch
  ```

  after the final `uv sync` in the Dockerfile (Dutch owns the Dockerfile). The script
  calls the reader's own download (`PipecatToneReaderModel.download`, the same
  `snapshot_download`), then loads the model and reads one line to prove it works.
- A model that cannot load is reported by Pipecat as an error that leaves the reader
  unusable; transcriptions pass through unread (no tone, no tip). The worker's
  `processor_unusable_policy` is the default `CONTINUE`, so the call goes on.

## Tests

- `bot/tests/test_tone.py`: mood steps, unsure and sarcastic readings, the trend label,
  the two step drop rule (from the calmest reading, re-arm, intake never tips), tips,
  the engine (mood messages, tip card through ranking and rate limit, replacement),
  relay and recorder, the intake tap, pipeline placement (caller only, after STT,
  threads=1, fp16), the switch, no RTVI user-input to the caller.
- `bot/tests/test_calls_db.py`: the reading saved on Postgres.
- Portal: `state/mood.test.ts`, `CallerPanel.test.tsx`, `InCall.test.tsx`,
  `AssistColumn.test.tsx`.
- Live: `server/tests/test_tone_live.py` (scripted Maria Lopez call with a frustrated
  turn; prints every reading, the tip, and bot RSS; `test_tone_baseline_reader_off`
  is the same call with the reader off).
