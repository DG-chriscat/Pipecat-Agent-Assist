/** Pure helpers for the caller panel (P3-3, AP-13). */

import type { CallerEvent } from "../api/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "1984-03-12" -> "Mar 12, 1984" (or "Mar 12" without the year). No timezone shift. */
export function formatIsoDate(iso: string, withYear = true): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const [, y, mo, d] = m;
  const month = MONTHS[Number(mo) - 1];
  if (!month) return iso;
  const day = `${month} ${Number(d)}`;
  return withYear ? `${day}, ${y}` : day;
}

export type EventTone = "info" | "ok" | "warn" | "bad";

/** Timeline dot color (DESIGN.md: glass blue info, phosphor done, bakelite warning, clay denied). */
export function eventTone(e: Pick<CallerEvent, "kind" | "status">): EventTone {
  if (e.kind === "call") return "info";
  if (e.status === "denied" || e.status === "expired") return "bad";
  if (e.status === "paid" || e.status === "approved") return "ok";
  return "warn";
}

export const SPARK_W = 220;
export const SPARK_H = 50;

/** Smooth path through 0..1 readings (0 calm at the bottom, 1 upset at the top). */
export function sparkPath(values: number[]): string {
  const pts = values.map((v, i) => {
    const x = (i / Math.max(1, values.length - 1)) * SPARK_W;
    const y = SPARK_H - 6 - Math.max(0, Math.min(1, v)) * (SPARK_H - 12);
    return [x, y] as const;
  });
  const f = (n: number) => n.toFixed(1);
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1];
    const [x1, y1] = pts[i];
    const cx = (x0 + x1) / 2;
    d += ` C${f(cx)} ${f(y0)} ${f(cx)} ${f(y1)} ${f(x1)} ${f(y1)}`;
  }
  return d;
}
