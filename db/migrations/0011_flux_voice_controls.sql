-- 0011_flux_voice_controls: P5-8, PRD AP-22. Flux voice controls on the Settings page.
--   tts_speed         Flux speaking rate, 0.8 to 1.2 on the page (the range the read
--                     back test passed, docs/spikes/tts-flux-controls.md); default 1.0.
--   tts_expressivity  Flux expressivity (Beta), -2 (calm) to 2 (animated); default 0.
--   slow_readback     speak identity and summary read backs, and lines with a member ID,
--                     date of birth, or claim number, at readback_speed; default on.
--   readback_speed    the read back rate, 0.8 to 1.0; default 0.9.
-- Ignored when tts_model is aura-2. Defaults live in code
-- (server/agent_assist_server/bot_settings.py); a row saved before this migration gets
-- them through the column defaults. The checks are the wider limits the code validates
-- inside. RLS on bot_settings is unchanged (0008).

ALTER TABLE bot_settings
    ADD COLUMN tts_speed double precision NOT NULL DEFAULT 1.0
        CHECK (tts_speed BETWEEN 0.5 AND 1.5),
    ADD COLUMN tts_expressivity smallint NOT NULL DEFAULT 0
        CHECK (tts_expressivity BETWEEN -2 AND 2),
    ADD COLUMN slow_readback boolean NOT NULL DEFAULT true,
    ADD COLUMN readback_speed double precision NOT NULL DEFAULT 0.9
        CHECK (readback_speed BETWEEN 0.8 AND 1.0);
