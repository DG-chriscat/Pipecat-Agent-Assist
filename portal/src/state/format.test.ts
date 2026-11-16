import { describe, expect, it } from "vitest";
import { fieldLabel, formatCell, isMoneyField } from "./format";

describe("record cell formatting (the bot sends raw connector values)", () => {
  it("money fields become dollars", () => {
    expect(isMoneyField("billed_amount")).toBe(true);
    expect(isMoneyField("amount")).toBe(true);
    expect(isMoneyField("paid_at")).toBe(false);
    expect(formatCell("billed_amount", 1240)).toBe("$1,240");
    expect(formatCell("amount", 89.5)).toBe("$89.50");
    expect(formatCell("amount", "312.4")).toBe("$312.40");
  });

  it("ISO dates and timestamps become short dates", () => {
    expect(formatCell("service_date", "2026-10-28")).toBe("Oct 28, 2026");
    expect(formatCell("paid_at", "2026-11-02T14:05:00+00:00")).toBe("Nov 2, 2026");
  });

  it("snake case statuses read as words; ids and codes stay as they are", () => {
    expect(formatCell("status", "pending_info")).toBe("Pending info");
    expect(formatCell("status", "denied")).toBe("Denied");
    expect(formatCell("claim_id", "CLM-900001")).toBe("CLM-900001");
    expect(formatCell("denial_code", "CO-197")).toBe("CO-197");
    expect(formatCell("notes", null)).toBe("–");
    expect(formatCell("visits_allowed", 6)).toBe("6");
  });

  it("field names become column labels", () => {
    expect(fieldLabel("claim_id")).toBe("Claim");
    expect(fieldLabel("service_date")).toBe("Service date");
    expect(fieldLabel("billed_amount")).toBe("Billed amount");
  });
});
