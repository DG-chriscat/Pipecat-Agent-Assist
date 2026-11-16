-- 0010_stt_models: P5-7, PRD IN-14. Speech to text model per side, on the Settings page.
--   stt_intake_model  the intake voice bot: nova-3-general (default, Silero VAD plus
--                     smart turn) or flux-general-en (Flux owns turn detection).
--   stt_assist_model  the listen only per person tracks after accept: nova-3-general
--                     (default), nova-3-medical, or flux-general-en.
-- Defaults live in code (server/agent_assist_server/bot_settings.py); a row saved before
-- this migration gets them through the column defaults. Numbered 0010 because identity
-- v2 (#50) takes 0009. RLS on bot_settings is unchanged (0008).

ALTER TABLE bot_settings
    ADD COLUMN stt_intake_model text NOT NULL DEFAULT 'nova-3-general'
        CHECK (stt_intake_model IN ('nova-3-general', 'flux-general-en')),
    ADD COLUMN stt_assist_model text NOT NULL DEFAULT 'nova-3-general'
        CHECK (stt_assist_model IN ('nova-3-general', 'nova-3-medical', 'flux-general-en'));
