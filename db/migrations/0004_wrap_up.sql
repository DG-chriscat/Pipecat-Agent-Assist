-- 0004_wrap_up: what the agent saves at wrap up (P3-5, AP-9) and who ended the call.
-- The queue write-through (server/agent_assist_server/queue_store.py) fills these.

ALTER TABLE calls
    ADD COLUMN ended_by       text CHECK (ended_by IN ('agent', 'caller')),
    ADD COLUMN wrap_up_reason text,
    ADD COLUMN wrap_up_notes  text,
    ADD COLUMN wrapped_up_at  timestamptz;
