"use client";

import { useEffect } from "react";
import {
  applyApprovalToExecution,
  failTaskInExecution,
  getEngineView,
  resetScope,
  resumeLoop,
  retryTaskInExecution,
  startLoop,
  stopLoop,
  tickOnce,
} from "@/lib/sim/engine";

/**
 * Test hook: browser E2E drives the deterministic engine through
 * `window.__engine` instead of waiting on wall-clock ticks.
 * Installed in development/test only — never in production builds.
 */
export function DevEngineHook() {
  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    (window as unknown as { __engine: unknown }).__engine = {
      startLoop,
      tickOnce,
      stopLoop,
      resumeLoop,
      resetScope,
      applyApprovalToExecution,
      failTaskInExecution,
      retryTaskInExecution,
      getEngineView,
    };
  }, []);
  return null;
}
