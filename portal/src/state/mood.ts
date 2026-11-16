/**
 * Mood (P4-5, AP-14): the caller's tone readings drawn as a trend.
 *
 * The bot reads each caller line with Pipecat Tone (from the words, not the audio)
 * and sends the whole trend as an assist `mood` message. A reading's `score` is its
 * mood step over 4: 0 Pleased, 0.25 Calm, 0.5 Uneasy, 0.75 Worried, 1 Frustrated.
 */
import type { MoodState, MoodTone } from "./assist";

/** Shown wherever the mood appears (DESIGN.md caller panel 5). */
export const MOOD_TOOLTIP = "Tone reader, words only.";

/** A bar at or above this is worried (bakelite): Uneasy and up. */
export const WORRIED = 0.5;

/** One bar per caller turn, 0 calm to 1 upset. */
export function moodBars(mood: MoodState | null | undefined): number[] {
  return mood ? mood.readings.map((r) => r.score) : [];
}

/** The call island chip: the trend label in its tone, a dash before any reading. */
export function moodChip(mood: MoodState | null | undefined): { text: string; tone: MoodTone | "neutral" } {
  if (!mood || !mood.label) return { text: "Mood –", tone: "neutral" };
  return { text: mood.label, tone: mood.tone ?? "neutral" };
}
