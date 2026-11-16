-- 0007_wrap_up_summary: P5-1, PRD AP-9. The call summary at wrap up.
--   wrap_up_draft    the bot's draft when the call ended: {"summary", "reason_code",
--                    "pack_id", "source" ("llm" or "fallback"), "drafted_at"}
--   wrap_up_summary  the summary as the agent saved it (edited or not)
-- Mock data only. No new table: the calls RLS from 0002 applies.

ALTER TABLE calls
    ADD COLUMN wrap_up_draft   jsonb,
    ADD COLUMN wrap_up_summary text;
