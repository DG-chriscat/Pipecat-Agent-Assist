import { describe, expect, it } from "vitest";
import { eventTone, formatIsoDate, sparkPath } from "./caller";

describe("formatIsoDate", () => {
  it("formats ISO dates without a timezone shift", () => {
    expect(formatIsoDate("1984-03-12")).toBe("Mar 12, 1984");
    expect(formatIsoDate("2026-11-09", false)).toBe("Nov 9");
    expect(formatIsoDate("2026-01-01")).toBe("Jan 1, 2026");
  });

  it("passes through anything it cannot parse", () => {
    expect(formatIsoDate("March 12")).toBe("March 12");
    expect(formatIsoDate("2026-13-01")).toBe("2026-13-01");
  });
});

describe("eventTone", () => {
  it("colors calls info, denials and expiries bad, paid and approved ok, the rest warn", () => {
    expect(eventTone({ kind: "call", status: "resolved" })).toBe("info");
    expect(eventTone({ kind: "claim", status: "denied" })).toBe("bad");
    expect(eventTone({ kind: "referral", status: "expired" })).toBe("bad");
    expect(eventTone({ kind: "claim", status: "paid" })).toBe("ok");
    expect(eventTone({ kind: "referral", status: "approved" })).toBe("ok");
    expect(eventTone({ kind: "claim", status: "pending_info" })).toBe("warn");
    expect(eventTone({ kind: "referral", status: "pending" })).toBe("warn");
  });
});

describe("sparkPath", () => {
  it("spans the full width, calm low and upset high", () => {
    const d = sparkPath([0, 1]);
    expect(d.startsWith("M0.0 44.0")).toBe(true);
    expect(d.endsWith("220.0 6.0")).toBe(true);
  });

  it("clamps readings outside 0..1", () => {
    expect(sparkPath([-3, 5])).toBe(sparkPath([0, 1]));
  });
});
