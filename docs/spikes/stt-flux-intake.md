# Flux STT for intake, Nova-3 for assist (card t_0cbf3922, P5-7, PRD IN-14)

Date: 2026-11-16. Pipecat 1.15.0, Deepgram `flux-general-en` (/v2/listen) and
`nova-3-general` / `nova-3-medical` (/v1/listen), Flux TTS Alexis, Anthropic (default
`create_llm` model), Daily `dg-chriscat`, Supabase Postgres (throwaway schema per run).
Host: 2 CPU, shared with other workers' test runs (load average is noted per batch).

## What changed

STT model per side, a Settings value each (Voice section, "Listening" group):

| Side | Setting | Default | Choices |
| --- | --- | --- | --- |
| Intake (voice bot) | `stt_intake_model` | `flux-general-en` | `flux-general-en`, `nova-3-general` |
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
