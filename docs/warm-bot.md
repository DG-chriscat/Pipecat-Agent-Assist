# Warm bot (P5-2)

A phone caller used to wait about 7 s from dialing to the greeting, mostly the bot's
cold start. The server now keeps a bot process already loaded and hands it the next call.

## Where the cold start goes

Measured on the build host (2 CPU) with `python -m agent_assist_bot`:

| step | seconds |
|---|---|
| import the bot (Pipecat, Daily, Deepgram, Flows, onnxruntime, ...) | 3.2 to 6.4 |
| Anthropic SDK, imported on first `create_llm` | ~3.1 |
| tone reader model (`pipecat-ai/tone-models` reader-rc2, fp16) | 1.3 to 2.5 |
| Silero VAD, packs, `Daily.init()` | ~0.3 |
| `build_worker` once the above are loaded | 0.4 to 0.6 |

## How it works

- `WarmBotPool` (`server/agent_assist_server/bot_launcher.py`) keeps `WARM_BOT_POOL` bots
  running (default 1, max 2, 0 turns the pool off). Each is
  `python -m agent_assist_bot --warm`.
- A warm bot (`bot/agent_assist_bot/warm.py`) imports the bot, then preloads the
  Anthropic SDK, Silero VAD, the tone model (into Pipecat Tone's per process model cache,
  so the call's two readers reuse it), the packs, and the Daily SDK. With
  `TONE_READER=0` the tone model is skipped. It then prints `agent_assist_bot warm ready`
  and blocks on stdin.
- A call (`POST /daily/dialin` or `POST /calls/browser`) answers the HTTP request first,
  then launches in the background:
  - a ready warm bot gets one JSON line on stdin: `room_url`, `token`, `session_id`,
    `call_id`, `call_domain` (null for a browser caller). The token never goes on argv
    or into a warm bot's environment.
  - none ready but one loading (Fly just woke the machine for this call): wait up to
    12 s for it, since a cold start next to it would load the same things while
    competing for the CPU.
  - otherwise a cold start, exactly as before.
- After a hand off the pool starts a replacement 5 s later, so the replacement's imports
  do not compete with the greeting. A warm bot that dies while idle is replaced; five
  in a row that die before getting ready stop the refills (calls start cold).
- The server's lifespan starts the pool and, on shutdown, stops the idle warm bots.
  Bots in a call are left alone, as before. On Fly (one machine, idle stop) the pool
  therefore comes up whenever the machine starts.
- Each idle warm bot holds about 310 MB (RSS). The Fly VM has 4 GB.

## Timing lines

The existing `[timing]` lines carry the comparison:

- server: `webhook_received` (dial-in), `call_requested` (browser, new), `room_created`,
  `bot_launched mode=warm|cold`
- bot: `warm_bot_assigned` (warm only, new), `bot_joined`, `dialin_ready`,
  `call_forwarded`, `caller_joined`, `greeting_tts_audio`, `greeting_first_audio`

Webhook to bot joined is `bot_joined - webhook_received`; dial to greeting on the phone
path is `greeting_first_audio - webhook_received` plus the carrier leg before the webhook.

## Live result (browser caller, build host, 2026-11-16)

`uv run pytest -m live server/tests/test_warm_bot_live.py -s -v`: one cold call, then two
warm calls (the second on the bot the pool refilled after the first).

```
call           mode  req->launched req->bot_joined req->greeting joined->greeting | caller: joined, heard
cold           cold          0.82s          12.36s        12.81s            0.45s | 11.74s, 13.08s
warm           warm          0.89s           3.41s         3.73s            0.32s | 2.82s, 4.03s
warm (refill)  warm          0.66s           2.57s         2.91s            0.34s | 2.03s, 3.10s
PASSED
```

`req` is the server's `call_requested`. "caller: heard" is when the harness caller heard
the greeting, measured from the test's POST. The ~0.7 s before `bot_launched` is room and
token creation at Daily. A warm bot still joins Daily and starts its pipeline (~2 s).

Phone path: Dutch asks Chris for one call after deploy and reads the same lines from
`fly logs` (`webhook_received` to `bot_joined`, and to `greeting_first_audio`).

## Fixed along the way

A caller hanging up during the greeting left the bot process running forever, even after
SIGTERM: intake start waited in Flows for the greeting to finish, inside Daily's join
handler, and the transport's cleanup waits for its handlers. Intake start now runs as
its own task, which the end paths cancel (`bot/agent_assist_bot/bot.py`). It reproduced
on main in every live browser call run here (`test_browser_call_live.py` failed waiting
for the bot to exit); with the fix the bot exits about 1 s after the caller leaves. A
leaked bot would also hold the CPU and memory the next warm bot needs.
