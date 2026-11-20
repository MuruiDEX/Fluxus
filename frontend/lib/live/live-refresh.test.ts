import { describe, expect, it } from "vitest";
import {
  LIVE_ACTIVE_MS,
  LIVE_IDLE_MS,
  LIVE_MAX_BACKOFF_MS,
  LIVE_SLOW_EVERY,
  combineRefreshReports,
  hasActiveWork,
  isSlowTick,
  liveIntervalMs,
} from "@/lib/live/live-refresh";

describe("liveIntervalMs", () => {
  it("uses the fast cadence while work is in flight and the slow one when idle", () => {
    expect(liveIntervalMs(true, 0)).toBe(LIVE_ACTIVE_MS);
    expect(liveIntervalMs(false, 0)).toBe(LIVE_IDLE_MS);
  });

  it("backs off exponentially on consecutive failures", () => {
    expect(liveIntervalMs(true, 1)).toBe(LIVE_ACTIVE_MS * 2);
    expect(liveIntervalMs(true, 2)).toBe(Math.min(LIVE_ACTIVE_MS * 4, LIVE_MAX_BACKOFF_MS));
    expect(liveIntervalMs(false, 1)).toBe(Math.min(LIVE_IDLE_MS * 2, LIVE_MAX_BACKOFF_MS));
  });

  it("caps the backoff and never returns less than the base cadence", () => {
    expect(liveIntervalMs(true, 99)).toBe(LIVE_MAX_BACKOFF_MS);
    expect(liveIntervalMs(false, 99)).toBe(LIVE_MAX_BACKOFF_MS);
    // A failed idle poll retries inside the ceiling instead of waiting 40s.
    expect(liveIntervalMs(false, 1)).toBe(LIVE_MAX_BACKOFF_MS);
    expect(liveIntervalMs(true, -3)).toBe(LIVE_ACTIVE_MS);
    expect(liveIntervalMs(true, Number.NaN)).toBe(LIVE_ACTIVE_MS);
  });
});

describe("isSlowTick", () => {
  it("fires on every Nth tick, never on the first", () => {
    expect(isSlowTick(0)).toBe(false);
    expect(isSlowTick(1)).toBe(false);
    expect(isSlowTick(LIVE_SLOW_EVERY)).toBe(true);
    expect(isSlowTick(LIVE_SLOW_EVERY * 2)).toBe(true);
    expect(isSlowTick(1.5)).toBe(false);
  });
});

describe("combineRefreshReports", () => {
  it("reports a failure when any resource failed, keeping the first message", () => {
    expect(
      combineRefreshReports([
        { outcome: "ok" },
        { outcome: "error", error: "Network unreachable" },
        { outcome: "error", error: "Second failure" },
      ]),
    ).toEqual({ outcome: "error", error: "Network unreachable" });
  });

  it("reports ok when at least one resource applied, and skipped otherwise", () => {
    expect(combineRefreshReports([{ outcome: "skipped" }, { outcome: "ok" }])).toEqual({
      outcome: "ok",
    });
    expect(combineRefreshReports([{ outcome: "skipped" }, { outcome: "skipped" }])).toEqual({
      outcome: "skipped",
    });
    expect(combineRefreshReports([])).toEqual({ outcome: "skipped" });
  });
});

describe("hasActiveWork", () => {
  it("is true while a task is executing", () => {
    expect(hasActiveWork([{ status: "working" }, { status: "completed" }], [])).toBe(true);
    expect(hasActiveWork([{ status: "not_started" }], [])).toBe(false);
  });

  it("is true while a hired agent is clearly busy, ignoring unhired agents", () => {
    expect(hasActiveWork([], [{ status: "thinking", hired: true }])).toBe(true);
    expect(hasActiveWork([], [{ status: "working", hired: false }])).toBe(false);
    expect(hasActiveWork([], [{ status: "available", hired: true }])).toBe(false);
  });
});
