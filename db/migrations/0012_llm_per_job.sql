-- 0012_llm_per_job: P5-9, PRD DC-11. The LLM for each job, on the Settings page.
--   llm_intake   the intake voice bot (Pipecat Flows function calls)
--   llm_assist   the live assist proposer and the Ask bar
--   llm_wrapup   the wrap up summary and reason code
-- Each is claude-haiku-5-5, phonellm-mini-alpha-2-fp8 (Deepgram phoneLLM mini, Early
-- Access, MIP opt out), or think-fast (Deepgram Think-Fast, Early Access, always stored
-- for Deepgram model training). Defaults live in code
-- (server/agent_assist_server/bot_settings.py) and were decided in
-- docs/spikes/llm-deepgram.md: phoneLLM mini for intake, Claude Haiku for assist and wrap
-- up. A row saved before this migration gets the same through the column defaults.
-- RLS on bot_settings is unchanged (0008).

ALTER TABLE bot_settings
    ADD COLUMN llm_intake text NOT NULL DEFAULT 'phonellm-mini-alpha-2-fp8'
        CHECK (llm_intake IN ('claude-haiku-5-5', 'phonellm-mini-alpha-2-fp8', 'think-fast')),
    ADD COLUMN llm_assist text NOT NULL DEFAULT 'claude-haiku-5-5'
        CHECK (llm_assist IN ('claude-haiku-5-5', 'phonellm-mini-alpha-2-fp8', 'think-fast')),
    ADD COLUMN llm_wrapup text NOT NULL DEFAULT 'claude-haiku-5-5'
        CHECK (llm_wrapup IN ('claude-haiku-5-5', 'phonellm-mini-alpha-2-fp8', 'think-fast'));
