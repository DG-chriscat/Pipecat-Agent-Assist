-- 0008_bot_settings: P5-5, PRD AP-20, IN-11. What the bot says and how it sounds,
-- changed on the portal's Settings page and read by the server when it hands a call
-- to a bot (so a call keeps the settings it started with).
--   bot_settings     one row (id 1); no row means the defaults in code
--                    (server/agent_assist_server/bot_settings.py). Reset deletes it.
--   settings_events  every save and reset: the login session that made it, the agent
--                    name when known, and the changed fields {"field": {"from", "to"}}.
-- Validation (voice in the model's catalog, AI disclosure in the greeting) is in code;
-- the checks here only keep the row well formed.

CREATE TABLE bot_settings (
    id                    smallint PRIMARY KEY CHECK (id = 1),
    tts_model             text NOT NULL CHECK (tts_model IN ('aura-2', 'flux')),
    tts_voice             text NOT NULL CHECK (char_length(tts_voice) BETWEEN 1 AND 64),
    greeting_text         text NOT NULL CHECK (char_length(greeting_text) BETWEEN 1 AND 400),
    intake_pack           text NOT NULL CHECK (char_length(intake_pack) BETWEEN 1 AND 64),
    identity_read_back    boolean NOT NULL,
    hold_update_text      text NOT NULL CHECK (char_length(hold_update_text) BETWEEN 1 AND 300),
    hold_update_secs      integer NOT NULL CHECK (hold_update_secs BETWEEN 5 AND 600),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            text
);

CREATE TABLE settings_events (
    event_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind                  text NOT NULL CHECK (kind IN ('save', 'reset')),
    session_id            text NOT NULL,
    agent                 text,
    changes               jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX settings_events_created_idx ON settings_events (created_at);

-- Same lock as 0002: keep the tables off Supabase's REST API.
ALTER TABLE bot_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings_events ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE format('REVOKE ALL ON TABLE %I.bot_settings FROM anon, authenticated', current_schema());
        EXECUTE format('REVOKE ALL ON TABLE %I.settings_events FROM anon, authenticated', current_schema());
    END IF;
END
$$;
