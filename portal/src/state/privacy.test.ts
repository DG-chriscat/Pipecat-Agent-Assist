import { describe, expect, it } from "vitest";
import { identityBadge, maskDob, maskMemberId, REVEAL_SECS } from "./privacy";

describe("PHI masking (AP-17)", () => {
  it("masks the member ID to its last two characters", () => {
    expect(maskMemberId("418302")).toBe("••••31");
    expect(maskMemberId("  evm100210 ")).toBe("••••10");
    expect(maskMemberId("7")).toBe("••••");
  });

  it("masks the date of birth to the year", () => {
    expect(maskDob("1984-03-12")).toBe("••/••/1984");
    expect(maskDob("March-ish")).toBe("••/••/••••");
  });

  it("re-masks after 30 seconds", () => {
    expect(REVEAL_SECS).toBe(30);
  });
});

describe("identity badge (AP-18)", () => {
  it("not checked when intake has no identity result", () => {
    expect(identityBadge(null, undefined)).toEqual({
      state: "unchecked",
      label: "Identity not checked",
      detail: "Identity was not checked at intake",
    });
  });

  it("3 of 3 when verified", () => {
    const b = identityBadge("verified", ["full_name", "dob", "member_id"]);
    expect(b.state).toBe("confirmed");
    expect(b.label).toBe("Identity 3 of 3");
    expect(b.detail).toBe("Name ✓ · Date of birth ✓ · Member ID ✓");
  });

  it("verified without the per check list still counts as 3 of 3", () => {
    expect(identityBadge("verified", undefined).label).toBe("Identity 3 of 3");
  });

  it("n of 3 from the checks that passed", () => {
    const b = identityBadge("not_verified", ["full_name", "member_id"]);
    expect(b.state).toBe("partial");
    expect(b.label).toBe("Identity 2 of 3");
    expect(b.detail).toBe("Name ✓ · Date of birth ✗ · Member ID ✓");
  });

  it("not found is 0 of 3", () => {
    const b = identityBadge("not_found", []);
    expect(b).toMatchObject({ state: "partial", label: "Identity 0 of 3" });
  });

  it("ignores unknown check names", () => {
    expect(identityBadge("not_verified", ["dob", "shoe_size"]).label).toBe("Identity 1 of 3");
  });
});
