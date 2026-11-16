import { afterEach, describe, expect, it, vi } from "vitest";
import {
  loadLiveOfficeTasksForProject,
  retryLiveOfficeApprovals,
  retryLiveOfficeProjects,
  retryLiveOfficeTasks,
} from "@/lib/live/live-office-actions";

describe("live office retry actions", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries only the projects loader with the business id", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => {});

    await retryLiveOfficeProjects("biz_1", load);

    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith("biz_1");
    expect(vi.getTimerCount()).toBe(0);
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("does not request tasks when the store business differs from the active business", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => {});

    await loadLiveOfficeTasksForProject(
      "biz_b",
      "proj_a1",
      "biz_a",
      load,
    );

    expect(load).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("requests only the verified selected project for the active business", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => {});

    await loadLiveOfficeTasksForProject(
      "biz_b",
      "proj_b1",
      "biz_b",
      load,
    );

    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith("biz_b", "proj_b1");
    expect(vi.getTimerCount()).toBe(0);
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("retries only the selected project's task loader", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => {});

    await retryLiveOfficeTasks("biz_1", "proj_1", load);

    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith("biz_1", "proj_1");
    expect(vi.getTimerCount()).toBe(0);
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("retries only the business approvals loader", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => {});

    await retryLiveOfficeApprovals("biz_1", load);

    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith("biz_1");
    expect(vi.getTimerCount()).toBe(0);
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);
  });
});
