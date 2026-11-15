-- 0003_queue_agents: agents table and calls.agent_id for the queue write-through
-- (PRD QH-1, QH-4; docs/queue.md). The in-memory queue service owns the state; these
-- rows are a copy for the portal, reporting, and debugging.

CREATE TABLE agents (
    agent_id              text PRIMARY KEY,
    name                  text NOT NULL,
    state                 text NOT NULL DEFAULT 'offline'
                          CHECK (state IN ('offline', 'available', 'ringing', 'on_call', 'wrap_up')),
    idle_since            timestamptz,
    current_call          text,
    updated_at            timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE calls ADD COLUMN agent_id text REFERENCES agents (agent_id);

-- Same lock as 0002: keep the table off Supabase's REST API.
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE format('REVOKE ALL ON TABLE %I.agents FROM anon, authenticated', current_schema());
    END IF;
END
$$;
