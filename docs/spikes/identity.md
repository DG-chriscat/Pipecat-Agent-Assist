# Identity capture fix (card t_91d9e7ea)

Date: 2026-11-16. Pipecat 1.15.0, Deepgram nova-3 STT and aura-2 TTS, Anthropic (default
`create_llm` model), Daily `dg-chriscat`, Supabase Postgres (throwaway schema per run,
migrated and seeded). Host: 2 CPU, other cards' work running at the same time (load
average up to 27 during the step 1 batch).

## What runs

`bot/tests/test_live_intake.py` (`@pytest.mark.live`), now parametrized over the 5 demo
personas in docs/db.md:

```bash
INTAKE_RUNS=2 INTAKE_RESULTS=/tmp/identity.jsonl \
  uv run pytest -m live bot/tests/test_live_intake.py -s -v
```

Each run starts the real bot process (`python -m agent_assist_bot`), joins as the
persona with the S1 harness, and plays Deepgram TTS lines. The script
(`bot/tests/harness/intake_script.py`) picks the next line from the calls row. At the
identity read back (details saved, `identity_result` still empty) the caller says yes,
or corrects the name and/or member ID if the saved value is wrong, spelling the first
name ("Aisha, A I S H A, Patel"). Daniel Okafor gives a wrong DOB on purpose (May 7,
not May 17), so the right result for him is `not_verified`.

Intake seconds run from both the caller and the bot being in the room to the calls row
going `ready_for_queue`. If intake never finished, it is the time until the harness
gave up. PRD targets: identity 10 of 10 correct on scripted calls, intake under 2
minutes.

## What changed

1. Read back before the check. `identity` now saves the details (`save_identity`) and
   moves to `identity_confirm`, which speaks a fixed TTS line: "Thanks. I have Maria
   Alvarez, member ID E V M, one zero zero, two three one. Is that right?" No LLM
   wording, so the ID is always in groups and the DOB is never read back. Yes runs
   `verify_identity`. A correction (`correct_identity`) saves the fixed fields and reads
   back once more; a second correction checks without a third read back. Asking for a
   person or an emergency during the read back still checks what was saved.
   `save_identity` with a missing field returns an error and stays (see final run 1).
2. Identity step STT tuning (`bot/agent_assist_bot/stt_tuning.py`), on only while the
   identity and identity_confirm nodes are active, back to defaults when the check
   runs or intake ends:
   - Deepgram `numerals=true`, `smart_format=true`, `endpointing=800` ms (defaults:
     false, false, Deepgram's 10 ms), sent as
     `STTUpdateSettingsFrame(delta=DeepgramSTTService.Settings(...), service=<intake stt>)`.
     Only the intake STT applies it; the listen only transcript STTs ignore it. Pipecat
     1.15 reconnects the Deepgram socket between turns and replays buffered audio.
   - Intake VAD `stop_secs` 0.2 s to 0.8 s (`VADAnalyzer.set_params`), so a pause
     between digit groups does not end the turn. Pipecat 1.15 has no runtime setting
     for `utterance_end_ms` that would help here (the turn is ended by VAD plus smart
     turn, not by Deepgram's utterance end), so this is the lever that matters.
   - Effect seen in the bot log: "member ID is EVM 1 0 0 2 3 1." arrives as one
     transcript, and the DOB as "03/12/1984". The identity prompt now says a date like
     03/12/1984 is MM/DD/YYYY.
3. Fixed keyterms from the active pack. Measured with a short `stt_keyterms` list on
   claims_status (EVM, CLM, "member ID", "claim number", the 6 plan names; no member
   names), since P4-2 was not merged yet. P4-2 merged while this PR was open, so the
   PR now uses P4-2's pack `keyterms` instead (a longer list: EVM, CLM, plan names,
   provider names, denial codes, claim terms); the bot passes the active pack's list to
   the intake STT for the whole call. The P4-2 list was not measured live here.

Intake time: the final read back no longer repeats the claim number, member ID, or DOB
(pack readback prompt plus the flow's readback instructions). On clean runs intake is
now about 100 s (was 105 to 110 s in Phase 2).

Steps 2 and 3 were measured together (one batch) to stay inside the card's runtime.

## Results

### Baseline (main at 0ffee62, harness only)

| Persona | Run | identity_result | Expected | Correct | Intake (s) | Heard name | Heard member ID |
|---|---|---|---|---|---|---|---|
| maria_alvarez | 1 | not_verified | verified | no | 179.4 | Murray Alvarez | EVM100231 |
| maria_alvarez | 2 | not_verified | verified | no | 251.4 | Maria Alvarez | EVM1002331 |
| james_chen | 1 | not_verified | verified | no | 86.1 | James Chen | EVM000232 |
| james_chen | 2 | verified | verified | yes | 97.9 | James Chen | EVM100232 |
| aisha_patel | 1 | not_verified | verified | no | 94.3 | Ayesha Patel | EVM100233 |
| aisha_patel | 2 | not_verified | verified | no | 100.7 | Ayesha Patel | EVM100233 |
| linda_nguyen | 1 | not_verified | verified | no | 100.2 | Linda Hengguyen | EVM100234 |
| linda_nguyen | 2 | verified | verified | yes | 96.2 | Linda Nguyen | EVM100234 |
| daniel_okafor | 1 | not_verified | not_verified | yes | 88.3 | Daniel Lokafor | EVM100235 |
| daniel_okafor | 2 | (none) | not_verified | no | 126.9 | (none) | (none) |

Identity correct 3 of 10. Intake under 120 s 7 of 10 (but 6 of those 7 had the wrong
identity). Names were misheard as often as member IDs.

### Step 1: read back only

| Persona | Run | identity_result | Expected | Correct | Intake (s) | Heard name | Heard member ID |
|---|---|---|---|---|---|---|---|
| maria_alvarez | 1 | not_verified | verified | no | 108.2 | Maria Alvarez | EVM100231 |
| maria_alvarez | 2 | verified | verified | yes | 176.4 | Maria Alvarez | EVM100231 |
| james_chen | 1 | verified | verified | yes | 140.3 | James Chen | EVM100232 |
| james_chen | 2 | verified | verified | yes | 96.8 | James Chen | EVM100232 |
| aisha_patel | 1 | verified | verified | yes | 146.4 | Aisha Patel | EVM100233 |
| aisha_patel | 2 | - | verified | no (Daily transport failed) | - | - | - |
| linda_nguyen | 1 | verified | verified | yes | 190.9 | Linda Nguyen | EVM100234 |
| linda_nguyen | 2 | (none) | verified | no | 92.3 | Linda Wayne | EVM100234 |
| daniel_okafor | 1 | (none) | not_verified | no (Daily transport failed) | 233.3 | (none) | (none) |
| daniel_okafor | 2 | (none) | not_verified | no (Daily transport failed) | 245.0 | (none) | (none) |

Identity correct 5 of 10. Maria run 1: the read back was right, but STT heard the DOB
as "eighteen eighty four" and the read back does not say the DOB. Maria run 2: the bot
asked for the claim number twice (intake 176 s). James run 1: the harness answers for
James's premium call did not fit the claims questions, so the bot kept re-asking the
service date (fixed in the script after this batch). Aisha 2 and both Daniel runs: Daily's media
transport failed ("signalling failed and will not reconnect", "recv transport changed
to failed") with load average about 27 on the 2 CPU host; not a code failure.

### Steps 2 + 3: numerals, smart format, endpointing on identity only, pack keyterms

| Persona | Run | identity_result | Expected | Correct | Intake (s) | Heard name | Heard member ID |
|---|---|---|---|---|---|---|---|
| maria_alvarez | 1 | (none) | verified | no | 254.5 | Maria Alvarez | EVM100231 |
| maria_alvarez | 2 | verified | verified | yes | 100.1 | Maria Alvarez | EVM100231 |
| james_chen | 1 | verified | verified | yes | 93.7 | James Chen | EVM100232 |
| james_chen | 2 | verified | verified | yes | 100.3 | James Chen | EVM100232 |
| aisha_patel | 1 | verified | verified | yes | 138.6 | Aisha Patel | EVM100233 |
| aisha_patel | 2 | verified | verified | yes | 180.3 | Aisha Patel | EVM100233 |
| linda_nguyen | 1 | verified | verified | yes | 134.4 | Linda Nguyen | EVM100234 |
| linda_nguyen | 2 | not_verified | verified | no | 135.3 | Linda Yuen | EVM100234 |
| daniel_okafor | 1 | not_verified | not_verified | yes | 101.1 | Daniel Okafor | EVM100235 |
| daniel_okafor | 2 | not_verified | not_verified | yes | 84.4 | Daniel Luca Ford | EVM100235 |

Identity correct 8 of 10. Intake under 120 s 5 of 10. The member ID was heard right
in 10 of 10 runs (baseline 7 of 10).

Daniel run 2 counts as correct because the result was the expected not_verified, but
STT also misheard his name ("Daniel Luca Ford"), so it would have been not_verified
with the right DOB too. Strictly, 7 of 10 runs captured every detail right.

Failures in this batch:

- Maria run 1 and Linda run 1: the LLM called `save_identity` with no arguments, so the
  read back said "member ID not caught". Maria then stalled until the 120 s idle
  timeout. Fixed after the batch: `save_identity` now rejects missing fields and asks
  again (`test_save_identity_with_missing_fields_asks_again_and_stays`). Not yet
  measured live.
- Linda run 2: STT kept hearing the last name wrong through 6 spelled-name corrections
  ("Linda Yuen"), then the bot checked and got not_verified. Aisha runs 1 and 2 had the
  same loop ("Ayesha") but the LLM eventually saved "Aisha". These loops are what push
  intake over 120 s (134 to 180 s). The two read back cap does not stop them: on most
  of those turns the LLM asked again in its own words instead of calling
  `correct_identity`, so the cap never counted them. The harness spells only the first
  name, so a misheard last name ("Nguyen") never gets spelled.

## Where this leaves the PRD targets

Not met. Identity is 8 of 10 (from 3 of 10), member IDs are now reliable, and clean
runs finish in about 100 s, but names are still the weak spot and each name correction
costs 12 to 20 s.

Options, for a decision (not done here):

1. Accept a name that is close to the member's when the member ID and DOB both match
   exactly (for example a phonetic or edit distance match: Ayesha/Aisha,
   Linda Yuen/Linda Nguyen). This changes the IN-6 identity rule, so it needs Chris or
   Dutch to decide.
2. Ask for the last name spelled as part of the identity question, not only on a
   correction. Costs about 5 s on every call.
3. Cap name corrections at one, then check and flag (the agent confirms the name), so a
   bad name never pushes intake over 120 s. Easy to add in `identity_confirm`.
