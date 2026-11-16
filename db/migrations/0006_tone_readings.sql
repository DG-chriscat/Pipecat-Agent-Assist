-- 0006_tone_readings: P4-5, PRD IN-10. One Pipecat Tone reading per caller line,
-- saved with the line. `transcript_lines.tone` (0001) holds the reader's emotion label
-- (neutral, happy, angry, ...); `tone_reading` holds the whole reading:
-- {"emotion", "confidence", "labels": [[label, confidence], ...], "sarcasm", "step",
--  "mood", "score"}. Read from the caller's words only, not the audio.
-- Agent lines and unread lines have both NULL. No new table: RLS from 0002 applies.

ALTER TABLE transcript_lines
    ADD COLUMN tone_reading jsonb;
