# Settings (P5-5, PRD AP-20, IN-11)

The portal's Settings page changes what the bot says and how it sounds. Saved settings
apply to the **next call**; a call in progress keeps the settings it started with.

Open it from the rail (gear icon, below History). A ringing, live, or wrap up call
always takes the agent back to Calls.

## What can be set

| Setting | Default | Rules (checked on the server; the page checks the same) |
| --- | --- | --- |
| `tts_model` | `flux` | `aura-2` or `flux` |
| `tts_voice` | `flux-alexis-en` | a voice of that model (`GET /settings/voices`) |
| `greeting_text` | the IN-2 greeting (intake.GREETING) | 1 to 400 characters; must say the caller is talking to an **AI** and that a **person will join** (IN-2) |
| `intake_pack` | `INTAKE_PACK` or `claims_status` | a pack in `packs/` |
| `identity_read_back` | on | on: read the name and member ID back before the identity check; off: check right after they are given |
| `hold_update_text` | `Thanks for waiting. {place} A person will be with you shortly.` | 1 to 300 characters; `{place}` is the only placeholder ("You're next in line.") |
| `hold_update_secs` | `HOLD_UPDATE_SECS` or 60 | whole seconds, 5 to 600 |
| `stt_intake_model` | `nova-3-general` | `nova-3-general` or `flux-general-en` (P5-7, IN-14) |
| `stt_assist_model` | `nova-3-general` | `nova-3-general`, `nova-3-medical`, or `flux-general-en` (P5-7, IN-14) |

Why Flux Alexis is the default: docs/spikes/tts-flux-vs-aura.md (Flux Alexis 80/80 key
entities, Aura-2 Thalia 79/80). The intake questions of the chosen pack are shown read
only; edit them in `packs/<id>.yaml` (docs/packs.md).

## Listening: speech to text per side (P5-7, IN-14)

The Voice section has a **Listening** group with two pickers, each with one line of
help:

- **Intake bot** (`stt_intake_model`): Nova-3 (default) or Flux. With Nova-3 the bot uses
  Silero VAD plus smart turn, as before P5-7; with Flux, Flux decides when the caller has
  finished (no Silero VAD, no smart turn). The caller can interrupt the bot either way.
  Flux was planned as the default; it stayed opt-in because it missed a hard last name
  on every run of the identity test (docs/spikes/stt-flux-intake.md).
- **Live assist** (`stt_assist_model`): Nova-3 (default), Nova-3 Medical, or Flux. These
  are the listen only streams per person track after an agent accepts. Flux there never
  interrupts anything and lines are still labeled by the track they came from.

There is no Flux medical model, so Nova-3 Medical is offered on the assist side only.
`GET /settings` lists the choices per side in `stt_models: {intake: [...], assist:
[...]}`; the page shows exactly those. How the bot builds each one, the identity step
tuning per model, and why eager end of turn is off: `bot/agent_assist_bot/stt.py` and
docs/spikes/stt-flux-intake.md.

## Voices

`GET /settings/voices` -> `{"aura-2": [voice], "flux": [voice]}`, each voice
`{id, name, accent, gender}`, English only.

- Aura-2: Deepgram's models API (`GET /v1/models`), cached after the first success; a
  known list if the API cannot be reached.
- Flux: the models API does not list Flux voices (Nov 16, 2026), so it is the documented
  English list (https://developers.deepgram.com/docs/flux-tts/voices), in
  `agent_assist_server/voice_catalog.py`. `server/tests/test_voice_catalog_live.py`
  speaks every Flux id on `/v2/speak` (36 of 36 passed on Nov 16).

Preview (`POST /settings/preview {tts_model, tts_voice, text?}`) synthesizes a sample
line (or the greeting being edited) on the server, `/v1/speak` or `/v2/speak` batch,
and returns MP3. The Deepgram key never reaches the browser.

## API (behind the login)

    GET  /settings          -> {values, defaults, is_default, updated_at, packs, limits,
                                stt_models}
    PUT  /settings          {values: {...all fields}, agent?: name} -> same shape
                            422 {detail: {errors: {field: message}}} when a field is wrong
    POST /settings/reset    {agent?: name} -> the defaults
    GET  /settings/voices
    POST /settings/preview  -> audio/mpeg

The bot key does not open these routes.

## Storage and the change log

- `bot_settings` (migration 0008; the STT columns in 0010): one row, `id = 1`. No row
  means the defaults in code
  (`agent_assist_server/bot_settings.py`). Reset deletes the row.
- `settings_events`: every save and reset, with `session_id` (a hash of the login
  cookie's nonce, never the cookie; "local" when the login is off), the agent's name
  from the portal, and the changed fields as `{"field": {"from", "to"}}`.
- Both tables have row level security on and no grants for Supabase's API roles.
- `SETTINGS_PERSIST=1` (set in fly.toml) stores them in Postgres; without it they are
  kept in the server's memory (local dev, tests). The server caches the row; saves go
  through it (one server process, like the queue).

## How a call gets its settings

The server reads the settings when it hands a call to a bot
(`bot_launcher.launch_in_background`, after `POST /calls/browser` or the dial-in
webhook), not when a warm bot starts (P5-2): a warm bot that has waited an hour still
gets what was saved when the call came in.

- Warm bot: in the assignment JSON line on stdin (`"settings": {...}`).
- Cold bot: as JSON in `AGENT_ASSIST_BOT_SETTINGS`.
- The server keeps each call's copy; wrap up reason codes come from that call's pack.
- If the settings cannot be read, the call goes ahead on the bot's defaults (logged).

The bot (`agent_assist_bot/call_settings.py`) logs one line per call, no free text:

    [settings] session=... tts=flux/flux-alexis-en pack=claims_status read_back=on hold_every=60s stt_intake=nova-3-general stt_assist=nova-3-general

A value it cannot use falls back to its default (logged); a call never fails over
settings. `build_tts` builds `DeepgramTTSService` (`/v1/speak`) for Aura-2 and
`DeepgramFluxTTSService` (`/v2/speak`) for Flux: Flux voice ids are refused on
`/v1/speak` (`V2_MODEL_ON_V1_SPEAK_ENDPOINT`), so the model picks the service class.
The greeting timing log, hold updates and music, the connecting line, and the gate are
the same for both.

## Live test

`server/tests/test_settings_flux_live.py`: sign in, open Settings, pick Flux, save a
custom greeting and hold message every 8 s, preview, then a browser call. Checks the bot
built Flux with the saved voice, the greeting heard is the saved one, intake reaches the
queue, the saved hold line plays, and "Connecting you now." follows the accept.
Screenshots: docs/screenshots/p5-5/.
