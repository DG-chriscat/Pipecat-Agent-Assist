/** Display formatting for record card cells: the bot sends raw connector values (docs/assist.md). */
import type { Cell } from "./assist";

const MONEY = /(^|_)(amount|balance|billed|allowed_amount|owed|premium|copay|cost)($|_)/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$/;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** Fields whose values are codes or ids: shown verbatim. */
const VERBATIM = /(^|_)(id|code|reference|number)$/;

export const isMoneyField = (field: string) => MONEY.test(field) && !field.endsWith("_at");

export function formatMoney(v: number): string {
  const whole = Number.isInteger(v);
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 })}`;
}

export function formatDate(v: string): string | null {
  const m = ISO_DATE.exec(v);
  if (!m) return null;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${month} ${Number(m[3])}, ${m[1]}` : null;
}

export function formatCell(field: string, v: Cell | undefined): string {
  if (v === null || v === undefined || v === "") return "–";
  if (isMoneyField(field)) {
    const n = typeof v === "number" ? v : Number(v);
    if (Number.isFinite(n)) return formatMoney(n);
  }
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  const date = formatDate(v);
  if (date) return date;
  if (VERBATIM.test(field)) return v;
  if (/^[a-z]+(_[a-z]+)*$/.test(v)) {
    const words = v.replace(/_/g, " ");
    return words[0].toUpperCase() + words.slice(1);
  }
  return v;
}

export function fieldLabel(field: string): string {
  const words = field.replace(/_id$/, "").replace(/_/g, " ");
  return words[0].toUpperCase() + words.slice(1);
}
