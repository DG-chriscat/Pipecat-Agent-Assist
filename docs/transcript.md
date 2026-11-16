# Live transcript (P3-4)

After an agent accepts, the call's bot goes listen only and sends a labeled transcript
to that agent's portal, and nobody else. Approach from spike S2 (docs/spikes/S2.md):
one Deepgram stream per person, each fed only that person's Daily track. No diarization.

## Bot

`bot/agent_assist_bot/transcript.py`, wired in `bot.build_worker`.

```
transport.input()                       # audio_in_user_tracks=True: one stream per participant
  -> ParallelPipeline(
       [AudioRoute(intake), Deepgram STT, TurnLogger],                 # before accept
       [AudioRoute(caller), Silero VAD, Deepgram STT, TranscriptRelay(caller)],
       [AudioRoute(agent),  Silero VAD, Deepgram STT, TranscriptRelay(agent)],
     )
  -> user aggregator -> gate -> LLM -> TTS -> transport.output()
```

- Before accept: `AudioRoute(intake)` passes the caller's audio to intake; the listen
  routes pass nothing.
- On the server's `agent_accepted` app message (sender `api` only) the bot says
  "Connecting you now.", the gate closes the LLM, `AudioRoute(intake)` stops, and the
  two listen routes open. Intake hears nothing more, so the bot never speaks again.
- `TranscriptRelay` drops its branch's audio and VAD frames so they never reach the
  intake aggregator, and turns STT frames into transcript messages.
- The listen branches are in the RTVI observer's `ignored_sources`: RTVI's built in
  `user-transcription` / `user-input` messages go to everyone in the room, the caller
  included, so the listen branches must not produce them.

### Roles

`CallRoles`, from the call flow, not from a display name anyone can set:

- caller: the first participant to join (the dial-in SIP leg, or the browser caller).
- agent: the participant whose Daily `user_name` equals the accepting agent's name in
  `agent_accepted`. The server mints the agent's meeting token with that same name
  (`mint_agent_token`), so it is set by the server, not typed by the caller. The
  caller's id never becomes the agent even with the same name. If the agent leaves
  and rejoins, the new participant id is matched again.

### Delivery

One Daily app message per update, addressed to the agent's participant id
(`DailyOutputTransportMessageUrgentFrame(participant_id=agent_id)`), never `*`.
With no agent in the room the line is not sent (still saved).

```
{"label": "rtvi-ai", "type": "server-message",
 "data": {"type": "transcript", "role": "caller" | "agent", "text": "...",
          "is_final": false, "ts": 1794790000.12, "line_id": "caller-3"}}
```

- `line_id` is `<role>-<n>`. Interim updates reuse the open line's id; the final with
  that id commits it, and the next update for that role opens `<role>-<n+1>`.
- `ts` is the bot's wall clock (epoch seconds) when the update was produced.
- Deepgram can split one spoken turn into several finals at pauses ("So what do I do
  now?" / "I cannot pay twelve hundred dollars."). They show as separate lines.

### Saved for wrap up

Final lines go to `transcript_lines` (`call_id`, `speaker_role`, `text`, `ts`) through
`CallStore.add_transcript_line`, from a queue so a slow write never stalls audio. The
queue is drained when the caller leaves. No audio is stored.

## Portal

- `call/media.ts` `onServerMessage(data)`; `DailyCallMedia` forwards the Pipecat client
  SDK's `onServerMessage`.
- `state/transcript.ts`: `parseTranscriptMessage`, `applyTranscript` (interim updates in
  place, a final commits, late interims never reopen a final), `segmentText`.
- `hooks/useCall.ts`: `call.transcript`, `call.highlights`, and
  `highlight(ranges: {lineId, start, end}[])`, the Phase 4 hook for trigger words
  (replaces the current set; `[]` clears).
- `components/TranscriptColumn.tsx` (DESIGN.md "Transcript (right)"): live chip, CALLER
  in fuchsia, YOU muted, fuchsia caret on a line still being spoken, trigger words
  underlined in fuchsia. Follows new lines while the agent is at the bottom; scrolling
  up pauses that (nothing moves under the cursor) and shows "Jump to latest".

## Tests

- `bot/tests/test_transcript.py`: role mapping, message shape, line ids, audio routing,
  agent-only delivery, persistence. `bot/tests/test_bot.py`: handler wiring and pipeline
  shape. `bot/tests/test_calls_db.py`: `add_transcript_line` on Postgres.
- Portal: `state/transcript.test.ts`, `components/TranscriptColumn.test.tsx` (labels,
  interim to final, highlight, autoscroll and its pause), `hooks/useCall.test.ts`,
  `CallFlow.test.tsx`.
- Live: `server/tests/test_live_transcript_live.py` (see the file header for the command).
  The portal agent speaks through its real browser mic path: `getUserMedia` is replaced
  with a WebAudio stream the test plays Deepgram TTS into.
