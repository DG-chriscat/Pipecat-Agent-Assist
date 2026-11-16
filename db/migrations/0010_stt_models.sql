-- 0010_stt_models: P5-7, PRD IN-14. Speech to text model per side, on the Settings page.
--   stt_intake_model  the intake voice bot: flux-general-en (default, Flux owns turn
--                     detection) or nova-3-general (Silero VAD plus smart turn).
--   stt_assist_model  the listen only per person tracks after accept: nova-3-general
--                     (default), nova-3-medical, or flux-general-en.
-- Defaults live in code (server/agent_assist_server/bot_settings.py); a row saved before
-- this migration gets them through the column defaults. Numbered 0010 because identity
-- v2 (#50) takes 0009. RLS on bot_settings is unchanged (0008).

ALTER TABLE bot_settings
    ADD COLUMN stt_intake_model text NOT NULL DEFAULT 'flux-general-en'
        CHECK (stt_intake_model IN ('flux-general-en', 'nova-3-general')),
    ADD COLUMN stt_assist_model text NOT NULL DEFAULT 'nova-3-general'
        CHECK (stt_assist_model IN ('nova-3-general', 'nova-3-medical', 'flux-general-en'));
