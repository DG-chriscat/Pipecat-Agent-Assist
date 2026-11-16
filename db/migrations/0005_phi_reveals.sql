-- 0005_phi_reveals: AP-17. Each time the agent reveals a masked field (member ID or
-- date of birth) the portal logs it on the call record: one jsonb entry
-- {"field", "agent_id", "at"} per reveal. The revealed value is never stored here.
-- The queue write-through (server/agent_assist_server/queue_store.py) fills it.

ALTER TABLE calls
    ADD COLUMN phi_reveals jsonb NOT NULL DEFAULT '[]'::jsonb;
