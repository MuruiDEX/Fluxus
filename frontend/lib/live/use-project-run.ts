"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  apiCommandProjectRun, apiGetProjectRun,
  type ApiProjectRun, type ProjectRunAction, type ProjectRunCommand,
} from "@/lib/api";
import {
  acceptsRunResponse, isLegacyRunRoute, reconcileRunCommandError, runMode,
} from "@/lib/live/project-run";
import type { LiveRefreshReport } from "@/lib/store/live";

interface Snapshot {
  key: string;
  run: ApiProjectRun | null;
  legacy: boolean;
  error: string | null;
}

export function useProjectRun(businessId: string, projectId: string | null) {
  const key = JSON.stringify([businessId, projectId]);
  const lifetime = useRef<{ key: string; read?: AbortController; write?: AbortController } | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [pending, setPending] = useState<{ key: string; action: ProjectRunAction } | null>(null);
  const [commandError, setCommandError] = useState<
    { key: string; message: string; assumeResponsibility: boolean } | null
  >(null);

  useEffect(() => {
    const scope = { key };
    lifetime.current = scope;
    return () => {
      if (lifetime.current === scope) {
        lifetime.current.read?.abort();
        lifetime.current.write?.abort();
        lifetime.current = null;
      }
    };
  }, [key]);

  const readRun = useCallback(async (afterCommand = false): Promise<LiveRefreshReport> => {
    const scope = lifetime.current;
    if (!projectId || scope?.key !== key || scope.read || (scope.write && !afterCommand)) {
      return { outcome: "skipped" };
    }
    const controller = new AbortController();
    scope.read = controller;
    const current = () => acceptsRunResponse(lifetime.current, scope, controller.signal);
    try {
      const run = await apiGetProjectRun(businessId, projectId, { signal: controller.signal });
      if (!current()) return { outcome: "skipped" };
      setSnapshot({ key, run, legacy: false, error: null });
      return { outcome: "ok" };
    } catch (error) {
      if (!current()) return { outcome: "skipped" };
      if (isLegacyRunRoute(error, businessId, projectId)) {
        setSnapshot((previous) => previous?.key === key && previous.run
          ? { ...previous, error: "Run status is unavailable." }
          : { key, run: null, legacy: true, error: null });
        return { outcome: "ok" };
      }
      const message = "Run status is unavailable.";
      setSnapshot((previous) => ({
        key, run: previous?.key === key ? previous.run : null, legacy: false, error: message,
      }));
      return { outcome: "error", error: message };
    } finally {
      if (scope.read === controller) scope.read = undefined;
    }
  }, [businessId, projectId, key]);
  const refresh = useCallback(() => readRun(), [readRun]);

  const visible = snapshot?.key === key ? snapshot : null;
  const run = visible?.run ?? null;
  const issue = useCallback(async (command: ProjectRunCommand) => {
    const scope = lifetime.current;
    if (!projectId || scope?.key !== key || scope.write || !run || visible?.error ||
      !run.allowedActions.includes(command.action)) return;
    const controller = new AbortController();
    scope.write = controller;
    scope.read?.abort();
    scope.read = undefined;
    const current = () => acceptsRunResponse(lifetime.current, scope, controller.signal);
    setPending({ key, action: command.action });
    setCommandError(null);
    try {
      const next = await apiCommandProjectRun(
        businessId, projectId, run.version, command, { signal: controller.signal },
      );
      if (current()) setSnapshot({ key, run: next, legacy: false, error: null });
    } catch (error) {
      if (!current()) return;
      const failure = await reconcileRunCommandError(error, () => readRun(true));
      if (current()) setCommandError({ key, ...failure });
    } finally {
      if (current()) {
        scope.write = undefined;
        setPending(null);
      }
    }
  }, [businessId, projectId, key, run, visible?.error, readRun]);

  return {
    run,
    mode: runMode(run, visible?.legacy),
    legacyRoute: visible?.legacy ?? false,
    error: visible?.error ?? null,
    pending: pending?.key === key ? pending.action : null,
    commandError: commandError?.key === key ? commandError : null,
    refresh,
    issue,
  };
}
