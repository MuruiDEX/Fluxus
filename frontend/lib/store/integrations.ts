"use client";

/**
 * LIVE integrations state (Stage 19) — provider catalog, connected accounts,
 * and the two owner-only lifecycle actions.
 *
 * Safety rules (all enforced here, never trusted to the caller):
 * - DEMO ban: loads and actions require an authenticated session whose
 *   membership list contains the target business. Anything else records an
 *   honest error and sends nothing.
 * - Reads: each load abandons the previous request, clears first (no stale
 *   flash after a business switch), and drops late responses via a sequence
 *   guard. Providers and connections are fetched together; a partial view is
 *   never presented as complete.
 * - Connect: exactly one POST per provider generation (single flight). The
 *   returned authorization URL is handed to the caller for a full browser
 *   navigation — the UI never guesses an OAuth URL and never touches tokens.
 * - Disconnect: no optimistic removal. The row leaves the cache only after
 *   the server confirms; the backend is the authority on token destruction.
 * - Honest errors: 401 flows through the shared session handler; 403 surfaces
 *   the owner-only rule; 503 surfaces missing server configuration; network
 *   loss is reported as unknown, never success.
 */

import { create } from "zustand";
import {
  ApiError,
  apiConnectIntegration,
  apiDisconnectIntegration,
  apiListIntegrationConnections,
  apiListIntegrationProviders,
  isAbortError,
  type ApiIntegrationConnection,
  type ApiIntegrationProvider,
} from "@/lib/api";
import { useSession } from "@/lib/store/auth";

export type IntegrationLoadStatus = "idle" | "loading" | "ready" | "error";
export type IntegrationActionPhase = "idle" | "working" | "done" | "error";

export interface IntegrationActionRecord {
  phase: IntegrationActionPhase;
  message: string | null;
  errorCode: string | null;
  updatedAt: number;
}

interface LiveIntegrationsState {
  status: IntegrationLoadStatus;
  businessId: string | null;
  providers: ApiIntegrationProvider[];
  connections: ApiIntegrationConnection[];
  error: string | null;
  load: (businessId: string) => Promise<void>;
  /** Drop one server-confirmed connection from the cache (same id only). */
  removeConnection: (connectionId: string) => void;
  reset: () => void;
}

interface IntegrationActionsState {
  records: Record<string, IntegrationActionRecord>;
  /**
   * Start OAuth for a provider. Resolves to the server-issued authorization
   * URL (the caller navigates there) or null when the request failed.
   */
  connect: (
    businessId: string,
    provider: string,
    includeWrites: boolean,
  ) => Promise<string | null>;
  disconnect: (businessId: string, connectionId: string) => Promise<void>;
  clear: (businessId: string, action: "connect" | "disconnect", target: string) => void;
}

const idleIntegrations = {
  status: "idle" as const,
  businessId: null,
  providers: [],
  connections: [],
  error: null,
};

let loadSeq = 0;
let loadController: AbortController | null = null;

function loadErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : "Request failed.";
}

export const useLiveIntegrations = create<LiveIntegrationsState>((set) => ({
  ...idleIntegrations,
  load: async (businessId) => {
    // Supersede any in-flight generation first: a refused load must never
    // leave a previous request alive to publish data after the refusal.
    loadController?.abort();
    loadController = null;
    const seq = ++loadSeq;

    // The Live view only mounts with a proven member business, but the guard
    // is repeated here so no other caller can read foreign installations.
    const session = useSession.getState();
    if (session.status !== "authenticated" || !session.businesses.includes(businessId)) {
      set({
        ...idleIntegrations,
        status: "error",
        businessId,
        error: "Sign in with access to this business to view integrations.",
      });
      return;
    }

    const controller = new AbortController();
    loadController = controller;
    const alive = () => loadSeq === seq && !controller.signal.aborted;
    set({ ...idleIntegrations, status: "loading", businessId });

    try {
      // Same signal for both reads: a superseded generation aborts together.
      const [providerList, connectionList] = await Promise.all([
        apiListIntegrationProviders(businessId, { signal: controller.signal }),
        apiListIntegrationConnections(businessId, { signal: controller.signal }),
      ]);
      if (!alive()) return;
      set({
        status: "ready",
        businessId,
        providers: providerList.providers,
        connections: connectionList.connections,
        error: null,
      });
    } catch (err) {
      if (isAbortError(err) || !alive()) return;
      set({
        status: "error",
        businessId,
        providers: [],
        connections: [],
        error: loadErrorMessage(err),
      });
    } finally {
      if (loadController === controller) loadController = null;
    }
  },
  removeConnection: (connectionId) =>
    set((state) =>
      state.connections.some((row) => row.id === connectionId)
        ? { connections: state.connections.filter((row) => row.id !== connectionId) }
        : state,
    ),
  reset: () => {
    loadController?.abort();
    loadSeq += 1;
    loadController = null;
    set({ ...idleIntegrations });
  },
}));

const idleAction: IntegrationActionRecord = {
  phase: "idle",
  message: null,
  errorCode: null,
  updatedAt: 0,
};

const flights: Record<string, Promise<unknown> | undefined> = {};
const actionSeqs: Record<string, number> = {};

function actionKey(businessId: string, action: string, target: string): string {
  return `${businessId}/${action}/${target}`;
}

function setAction(key: string, patch: Partial<IntegrationActionRecord>): void {
  useLiveIntegrationsActions.setState((state) => ({
    records: {
      ...state.records,
      [key]: { ...state.records[key], ...idleAction, ...patch, updatedAt: Date.now() },
    },
  }));
}

/**
 * Shared demo/session gate for both actions. Returns an error record when the
 * caller must not send anything.
 */
function membershipFailure(
  businessId: string,
): { message: string; errorCode: string } | null {
  const session = useSession.getState();
  if (session.status !== "authenticated") {
    return { message: "Sign in to manage integrations.", errorCode: "unauthorized" };
  }
  if (!session.businesses.includes(businessId)) {
    return { message: "No access to this business.", errorCode: "forbidden" };
  }
  return null;
}

function actionFailure(err: unknown): { message: string; errorCode: string | null } {
  if (!(err instanceof ApiError)) {
    return { message: "Unexpected client error.", errorCode: null };
  }
  if (err.status === 0) {
    return {
      message: "Lost contact with the server — outcome unknown. Reload to reconcile.",
      errorCode: err.code,
    };
  }
  if (err.status === 403) {
    return {
      message: "Only the business owner can manage integration connections.",
      errorCode: err.code,
    };
  }
  if (err.status === 503) {
    return {
      message: "The server is not configured for this integration yet.",
      errorCode: err.code,
    };
  }
  return { message: err.message, errorCode: err.code };
}

async function connectWork(
  key: string,
  businessId: string,
  provider: string,
  includeWrites: boolean,
  seq: number,
): Promise<string | null> {
  const alive = () => actionSeqs[key] === seq;
  const finish = (patch: Partial<IntegrationActionRecord>) => {
    if (alive()) setAction(key, patch);
  };

  const blocked = membershipFailure(businessId);
  if (blocked) {
    finish({ phase: "error", ...blocked });
    return null;
  }

  setAction(key, { phase: "working", message: null, errorCode: null });
  try {
    const answer = await apiConnectIntegration(businessId, provider, includeWrites);
    if (!alive()) return null;
    // Defense in depth: never navigate to a non-https origin supplied by a
    // server or proxy. OAuth authorization URLs are always https in practice.
    if (!/^https:\/\//i.test(answer.authorizationUrl)) {
      finish({
        phase: "error",
        message: "The server returned an unusable authorization URL — nothing was sent.",
        errorCode: "invalid_authorization_url",
      });
      return null;
    }
    finish({
      phase: "done",
      message: includeWrites
        ? "Redirecting to the provider to grant read and write access."
        : "Redirecting to the provider to grant read-only access.",
      errorCode: null,
    });
    return answer.authorizationUrl;
  } catch (err) {
    if (!alive()) return null;
    const failure = actionFailure(err);
    finish({ phase: "error", ...failure });
    return null;
  }
}

async function disconnectWork(
  key: string,
  businessId: string,
  connectionId: string,
  seq: number,
): Promise<void> {
  const alive = () => actionSeqs[key] === seq;
  const finish = (patch: Partial<IntegrationActionRecord>) => {
    if (alive()) setAction(key, patch);
  };

  const blocked = membershipFailure(businessId);
  if (blocked) {
    finish({ phase: "error", ...blocked });
    return;
  }

  setAction(key, { phase: "working", message: null, errorCode: null });
  try {
    const answer = await apiDisconnectIntegration(businessId, connectionId);
    if (!alive()) return;
    if (answer.outcome !== "disconnected") {
      finish({
        phase: "error",
        message: `Unexpected answer "${answer.outcome}" — reloaded, nothing assumed.`,
        errorCode: "internal",
      });
      return;
    }
    // Confirmed by the server: remove exactly this row from the cache.
    const live = useLiveIntegrations.getState();
    if (live.businessId === businessId) live.removeConnection(connectionId);
    finish({
      phase: "done",
      message: "Disconnected. Stored credentials were discarded by the server.",
      errorCode: null,
    });
  } catch (err) {
    if (!alive()) return;
    const failure = actionFailure(err);
    finish({ phase: "error", ...failure });
  }
}

export const useLiveIntegrationsActions = create<IntegrationActionsState>((set) => ({
  records: {},
  connect: (businessId, provider, includeWrites) => {
    const key = actionKey(businessId, "connect", provider);
    const flight = flights[key] as Promise<string | null> | undefined;
    if (flight) return flight;
    const seq = (actionSeqs[key] ?? 0) + 1;
    actionSeqs[key] = seq;
    const promise = connectWork(key, businessId, provider, includeWrites, seq).finally(() => {
      if (flights[key] === promise) delete flights[key];
    });
    flights[key] = promise;
    return promise;
  },
  disconnect: (businessId, connectionId) => {
    const key = actionKey(businessId, "disconnect", connectionId);
    const flight = flights[key] as Promise<void> | undefined;
    if (flight) return flight;
    const seq = (actionSeqs[key] ?? 0) + 1;
    actionSeqs[key] = seq;
    const promise = disconnectWork(key, businessId, connectionId, seq).finally(() => {
      if (flights[key] === promise) delete flights[key];
    });
    flights[key] = promise;
    return promise;
  },
  clear: (businessId, action, target) => {
    const fullKey = actionKey(businessId, action, target);
    actionSeqs[fullKey] = (actionSeqs[fullKey] ?? 0) + 1;
    set((state) => {
      if (!(fullKey in state.records)) return state;
      const records = { ...state.records };
      delete records[fullKey];
      return { records };
    });
  },
}));
