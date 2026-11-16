-- 0004_assist_events: what the assist showed the agent and what the agent did with it
-- (P4-3, PRD DC-6, DC-7, DC-9, AP-7). One row per event, in order:
--   card_sent  a card reached the agent (data: kind, rank, trigger, latency_ms)
--   accept     the agent accepted a card; for a next best step, data.action_id is the
--              simulated action (DC-9: nothing is written to source systems)
--   dismiss, pin, unpin, rate (data.rating up/down)
--   guardrail  a rendered card was blocked as clinical advice
-- The call record keeps the history; source tables are never touched.

CREATE TABLE assist_events (
    event_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    call_id               uuid NOT NULL REFERENCES calls (call_id) ON DELETE CASCADE,
    kind                  text NOT NULL
                          CHECK (kind IN ('card_sent', 'accept', 'dismiss', 'pin', 'unpin', 'rate', 'guardrail')),
    card_id               text,
    data                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assist_events_call_idx ON assist_events (call_id, event_id);

-- Same lock as 0002: keep the table off Supabase's REST API.
ALTER TABLE assist_events ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE format('REVOKE ALL ON TABLE %I.assist_events FROM anon, authenticated', current_schema());
    END IF;
END
$$;
