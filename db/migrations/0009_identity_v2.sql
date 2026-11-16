-- 0009_identity_v2: PRD IN-6 (updated) and IN-13 (Chris, Nov 16).
--
-- identity_result gains:
--   close_match  member ID and date of birth exact, name close by spelling
--   incomplete   a detail was still missing after the one correction
-- The agent's human verification decision on handoff (IN-13) goes on the call record
-- twice: the bot logs an assist_events row (kind identity_decision, data: decision,
-- by, at, intake_result) and the server stores it on calls.identity_decision
-- ({"decision": "verified" | "not_verified", "agent_id", "agent_name", "at"}).
-- No new table: RLS from 0002 and 0004 applies.

ALTER TABLE calls DROP CONSTRAINT calls_identity_result_check;
ALTER TABLE calls ADD CONSTRAINT calls_identity_result_check
    CHECK (identity_result IN ('verified', 'close_match', 'not_verified', 'not_found', 'incomplete'));

ALTER TABLE calls
    ADD COLUMN identity_decision jsonb;

ALTER TABLE assist_events DROP CONSTRAINT assist_events_kind_check;
ALTER TABLE assist_events ADD CONSTRAINT assist_events_kind_check
    CHECK (kind IN ('card_sent', 'accept', 'dismiss', 'pin', 'unpin', 'rate', 'guardrail',
                    'identity_decision'));
