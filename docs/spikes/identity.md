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

## v2: close match, one fix, agent verification (card t_978435cf, Chris Nov 16)

Options 1 and 3 above were approved (PRD IN-6 updated, IN-13 new).

- **Close match** (`calls.name_part_close`): member ID and DOB exact, name not exact.
  One of first/last must match exactly; the other must be within 3 edits (Levenshtein)
  with normalized similarity of at least 0.5 (1 - edits / longer length). Pure Python, no
  new dependency (a Double Metaphone package was considered; edit distance separates
  the spike's cases well enough and is easier to reason about). Examples:

  | heard | on file | edits, similarity | result |
  | --- | --- | --- | --- |
  | Linda Yuen | Linda Nguyen | 3, 0.50 | close_match |
  | Linda Hengguyen | Linda Nguyen | 3, 0.67 | close_match |
  | Ayesha Patel | Aisha Patel | 2, 0.67 | close_match |
  | Daniel Lokafor | Daniel Okafor | 1, 0.86 | close_match |
  | Maria Alvares | Maria Alvarez | 1, 0.86 | close_match |
  | Linda Yuan | Linda Nguyen | 4, 0.33 | not_verified |
  | Linda Wayne / Linda Win | Linda Nguyen | 0.17 | not_verified |
  | Daniel Luca Ford | Daniel Okafor | 4, 0.33 | not_verified |
  | Ayesha Pattel | Aisha Patel | both parts differ | not_verified |
  | Linda (one word) | Linda Nguyen | | not_verified |

  A wrong DOB or another member's ID is never a close match, and name alone never
  identifies (the two James Chens still need DOB and ID). Unit tests:
  `bot/tests/test_identity.py`.
- **One fix** (`intake.MAX_IDENTITY_FIXES = 1`): a `correct_identity`, or one free
  form re-ask by the LLM in `identity_confirm`, then the bot checks and moves on (no
  second read back). A `save_identity` missing a field is refused once; the second time
  it checks what was given (`incomplete`).
- **Agent verification** (IN-13, docs/assist.md): any result but verified gives the
  agent a verify hero first; records stay locked until Mark verified.

### v2 harness (feat/identity-v2, Nov 16, 5 personas x 2)

`INTAKE_RUNS=2 uv run pytest -m live bot/tests/test_live_intake.py`. "Correct" now
counts close_match for the real member (the agent confirms the name); Daniel's wrong DOB
must still be not_verified.

| persona | run | result | correct | intake s | heard name | caller lines |
| --- | --- | --- | --- | --- | --- | --- |
| Maria | 1 | verified | yes | 109.7 | Maria Alvarez | identity, fix_member_id, then reason... |
| Maria | 2 | (none) | no | 24.7 | | identity; LLM called report_emergency |
| James | 1 | (none) | no | 78.6 | | identity; Daily/Deepgram transport failure (1011, SFU ChannelNotOpen) |
| James | 2 | verified | yes | 116.0 | James Chen | identity, confirm_yes |
| Aisha | 1 | close_match | yes | 97.1 | Ayesha Patel | identity, fix_name |
| Aisha | 2 | close_match | yes | 102.3 | Ayesha Patel | identity, fix_name |
| Linda | 1 | (none) | no | 25.1 | | identity; LLM called report_emergency |
| Linda | 2 | verified | yes | 131.8 | Linda Nguyen | identity (missing field, asked again), identity, confirm_yes |
| Daniel | 1 | not_verified | yes | 124.3 | Daniel | identity, fix_both |
| Daniel | 2 | not_verified | yes | 99.7 | Daniel Okafor | identity, confirm_yes |

- **Identity correct: 7 of 10; 7 of 7 runs that reached the check.** Aisha, heard as
  "Ayesha" both times, is now close_match after one correction (v1: not_verified or a
  loop). No run looped on a name.
- **Intake under 120 s: 5 of the 7 completed runs.** Linda 2 (131.8 s) spent one
  re-ask on a `save_identity` with a missing field; Daniel 1 (124.3 s) had the LLM speak
  its own reasoning aloud before the read back (a 30 s turn).
- **Not met: 10 of 10 and 9 of 10.** The three misses are not identity checks:
  - Maria 2 and Linda 1: on a clean first identity line ("My name is Linda Nguyen. My
    date of birth is 01/30/1958, and my member ID is EVM 1 0 0 2 3 4."), the intake LLM
    called `report_emergency`, so the call went straight to the emergency line. The
    identity node and the emergency tool are unchanged on this branch; v1 runs never
    showed this. Not fixed here (suppressing an emergency flag is a safety decision):
    follow-up card.
  - James 1: Daily SFU and Deepgram 1011 timeouts mid identity line, host load ~5 on
    the 2 CPU build host.
- **Live agent side** (`server/tests/test_identity_verify_live.py`, passed): a Linda call
  that says "Linda Yuen" (STT heard "Linda Yuan", not_verified, ID matched). The verify
  hero showed 3.19 s after Enter, with Member ID and DOB matched and the name heard vs on
  file. The caller's question produced a locked tile 3.71 s later, with no claim
  content. Mark verified unlocked CLM-900005 and showed the appeal next step, the caller
  panel switched to Linda Nguyen "Verified by agent", and Reveal worked.
  `calls.identity_decision` and the `assist_events` `identity_decision` row were
  written. Screenshots: docs/screenshots/identity-v2/.
