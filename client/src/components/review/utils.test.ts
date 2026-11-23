import { afterEach, describe, expect, it, vi } from "vitest";

import { formatAsrTime } from "./utils";

afterEach(() => {
  vi.useRealTimers();
});

describe("formatAsrTime", () => {
  it("shows time only for today", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-27T10:44:00+08:00"));

    expect(formatAsrTime("2026-07-27T09:15:00+08:00")).toBe("09:15");
  });

  it("shows date only for other days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-27T10:44:00+08:00"));

    expect(formatAsrTime("2026-07-26T23:59:00+08:00")).toBe("7/26");
  });
});
