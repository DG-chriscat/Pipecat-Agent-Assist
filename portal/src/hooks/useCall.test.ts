import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { AcceptResult } from "../api/types";
import { createFakeMedia, FakeMedia } from "../test/fakeMedia";
import { useCall } from "./useCall";

const res: AcceptResult = {
  session_id: "s1",
  room_url: "https://x.daily.co/r1",
  token: "t",
  expires_at: 0,
  summary: { full_name: "Maria Alvarez", reason: "MRI claim" },
  outcome: "completed",
  identity_result: "verified",
  member_id: "EVM100231",
  flags: [],
};

const tmsg = (line_id: string, text: string, is_final = true) => ({
  type: "transcript",
  role: line_id.split("-")[0],
  text,
  is_final,
  ts: 1,
  line_id,
});

describe("useCall transcript (P3-4)", () => {
  beforeEach(() => FakeMedia.reset());

  it("collects transcript lines from server messages and starts empty on each call", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    expect(result.current.call?.transcript).toEqual([]);
    act(() => FakeMedia.last().events.onServerMessage(tmsg("caller-1", "Hi.")));
    expect(result.current.call?.transcript.map((l) => l.text)).toEqual(["Hi."]);
    act(() => result.current.start({ ...res, session_id: "s2" }));
    expect(result.current.call?.transcript).toEqual([]);
  });

  it("ignores messages from a media session that is no longer current", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    const old = FakeMedia.last();
    act(() => result.current.start({ ...res, session_id: "s2" }));
    act(() => old.events.onServerMessage(tmsg("caller-1", "stale")));
    expect(result.current.call?.transcript).toEqual([]);
  });

  it("highlight(ranges) sets the trigger word ranges; a new call clears them", () => {
    const { result } = renderHook(() => useCall(createFakeMedia));
    act(() => result.current.start(res));
    act(() => result.current.highlight([{ lineId: "caller-1", start: 0, end: 3 }]));
    expect(result.current.call?.highlights).toEqual([{ lineId: "caller-1", start: 0, end: 3 }]);
    act(() => result.current.start({ ...res, session_id: "s2" }));
    expect(result.current.call?.highlights).toEqual([]);
  });
});
