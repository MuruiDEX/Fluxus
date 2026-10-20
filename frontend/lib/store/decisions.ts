"use client";

/**
 * LIVE approval decision runner (Stage 7.7) — drives the existing backend
 * `POST .../approvals/{id}/approve|reject` endpoints.
 *
 * Safety rules (all enforced here, never trusted to the caller):
 * - DEMO ban: decides only with an authenticated session whose membership
 *   list contains the target business. Anything else records an error and
 *   sends nothing.
 * - Client precheck: a cached non-pending approval is never re-POSTed — the
 *   record reflects the known terminal state instead.
 * - Single flight per approval: concurrent decide() calls (double click,
 *   StrictMode, remount) share one promise — exactly one POST per generation.
 * - Server adjudicates: `already_*` outcomes reconcile from the answer,
 *   409 `conflict` reloads the list (state changed elsewhere), errors record
 *   honest messages with retry. The repeat of one decision is idempotent by
 *   server contract, so retry is always safe.
 * - Sync: a decided approval is applied to the LIVE cache; the LIVE activity
 *   feed reloads only if already loaded for the same business (full reload
 *   from the server — events are never fabricated or deduplicated locally).
 * - Stale generations are dropped by sequence guard. Never runs the linked
 *   task: decisions and executions stay separate by construction (this module
 *   does not import the execution runner).
 */

import { create } from "zustand";
import {
  ApiError,
  apiApproveApproval,
  apiRejectApproval,
  decisionOutcomeOf,
  isAbortError,
  type ApiDecision,
} from "@/lib/api";
import { useLiveActivity, useLiveApprovals } from "@/lib/store/live";
import { useSession } from "@/lib/store/auth";
import type { ApprovalStatus } from "@/lib/domain/types";

export type Decision = "approved" | "rejected";
export type DecisionPhase = "idle" | "deciding" | "decided" | "error";

export interface ApprovalDecision {
  phase: DecisionPhase;
  /** Last requested decision. */
  decision: Decision | null;
  /** Known server status after reconcile, if any. */
  status: ApprovalStatus | null;
  message: string | null;
  errorCode: string | null;
  updatedAt: number;
}

interface DecisionsState {
  records: Record<string, ApprovalDecision>;
  decide: (businessId: string, approvalId: string, decision: Decision) => Promise<void>;
  clear: (businessId: string, approvalId: string) => void;
}

const idleDecision: ApprovalDecision = {
  phase: "idle",
  decision: null,
  status: null,
  message: null,
  errorCode: null,
  updatedAt: 0,
};

function keyOf(businessId: string, approvalId: string): string {
  return `${businessId}/${approvalId}`;
}

const flights: Record<string, Promise<void> | undefined> = {};
const seqs: Record<string, number> = {};

function setDecision(key: string, patch: Partial<ApprovalDecision>): void {
  useLiveDecisions.setState((s) => ({
    records: { ...s.records, [key]: { ...s.records[key], ...idleDecision, ...patch, updatedAt: Date.now() } },
  }));
}

/** Reload the activity feed only when it is already open for this business. */
async function refreshActivityIfLoaded(businessId: string): Promise<void> {
  const activity = useLiveActivity.getState();
  if (activity.businessId === businessId && activity.status !== "idle") {
    await activity.load({ businessId, projectId: activity.projectId ?? undefined });
  }
}

/** Reload the approvals list to reconcile against server truth. */
async function reloadApprovals(businessId: string): Promise<void> {
  await useLiveApprovals.getState().load(businessId);
}

function terminalStatusOf(outcome: ApiDecision["outcome"]): ApprovalStatus | null {
  if (outcome === "approved" || outcome === "already_approved") return "approved";
  if (outcome === "rejected" || outcome === "already_rejected") return "rejected";
  return null;
}

async function decideWork(
  key: string,
  businessId: string,
  approvalId: string,
  decision: Decision,
  seq: number,
): Promise<void> {
  const alive = () => seqs[key] === seq;
  const finish = (patch: Partial<ApprovalDecision>) => {
    if (alive()) setDecision(key, patch);
  };

  // --- Gate 1: proven session + membership (DEMO ban). ---
  const session = useSession.getState();
  if (session.status !== "authenticated") {
    finish({ phase: "error", decision, message: "Sign in to decide approvals.", errorCode: "unauthorized" });
    return;
  }
  if (!session.businesses.includes(businessId)) {
    finish({ phase: "error", decision, message: "No access to this business.", errorCode: "forbidden" });
    return;
  }

  // --- Gate 2: client precheck against LIVE scope data. Terminal approvals
  // are never re-POSTed from the UI; the record reflects known state. ---
  const cached = useLiveApprovals.getState().approvals.find((a) => a.id === approvalId);
  if (cached && cached.status !== "pending") {
    const same = (cached.status === "approved" && decision === "approved") ||
      (cached.status === "rejected" && decision === "rejected");
    finish({
      phase: "decided",
      decision,
      status: cached.status,
      message: same
        ? `Already ${cached.status} — confirmed from local state, nothing sent.`
        : `Already ${cached.status} — terminal states never reopen.`,
      errorCode: same ? null : "conflict",
    });
    return;
  }

  // --- POST: exactly one flight; the server CAS adjudicates races. ---
  setDecision(key, { phase: "deciding", decision, status: null, message: null, errorCode: null });
  let answer: ApiDecision;
  try {
    answer = decision === "approved"
      ? await apiApproveApproval(businessId, approvalId)
      : await apiRejectApproval(businessId, approvalId);
  } catch (err) {
    if (!alive()) return;
    if (isAbortError(err)) return;
    if (!(err instanceof ApiError)) {
      finish({ phase: "error", decision, message: "Unexpected client error.", errorCode: null });
      return;
    }
    if (err.status === 0) {
      finish({
        phase: "error",
        decision,
        message: "Lost contact with the server — outcome unknown. Retry to reconcile.",
        errorCode: err.code,
      });
      return;
    }
    if (err.status === 409 && decisionOutcomeOf(err.payload) === "conflict") {
      try {
        await reloadApprovals(businessId);
      } catch {
        // Best effort: the message below still describes the conflict.
      }
      if (!alive()) return;
      finish({
        phase: "error",
        decision,
        message: "Changed on the server — this approval is already decided. List refreshed.",
        errorCode: "conflict",
      });
      return;
    }
    finish({ phase: "error", decision, message: err.message, errorCode: err.code });
    return;
  }
  if (!alive()) return;

  // --- 2xx answers: approved/rejected/already_*. Apply + sync. ---
  const status = terminalStatusOf(answer.outcome);
  if (status) {
    useLiveApprovals.getState().applyDecision(approvalId, status);
    // Awaited: decide() resolves only after approvals + activity reflect the
    // confirmed outcome, so the UI never shows a decided card with stale feed.
    await refreshActivityIfLoaded(businessId);
    if (!alive()) return;
    const repeated = answer.outcome === "already_approved" || answer.outcome === "already_rejected";
    finish({
      phase: "decided",
      decision,
      status,
      message: repeated
        ? `Already ${status} — confirmed by the server, no duplicate recorded.`
        : `Recorded as ${status}, with an audit event.`,
      errorCode: null,
    });
    return;
  }
  finish({
    phase: "error",
    decision,
    message: `Unexpected answer "${answer.outcome}" — state reloaded, nothing assumed.`,
    errorCode: "internal",
  });
  try {
    await reloadApprovals(businessId);
  } catch {
    // Best effort only.
  }
}

export const useLiveDecisions = create<DecisionsState>(() => ({
  records: {},
  decide: (businessId, approvalId, decision) => {
    const key = keyOf(businessId, approvalId);
    const flight = flights[key];
    if (flight) return flight;
    const seq = (seqs[key] ?? 0) + 1;
    seqs[key] = seq;
    const promise = decideWork(key, businessId, approvalId, decision, seq).finally(() => {
      if (flights[key] === promise) delete flights[key];
    });
    flights[key] = promise;
    return promise;
  },
  clear: (businessId, approvalId) => {
    const key = keyOf(businessId, approvalId);
    seqs[key] = (seqs[key] ?? 0) + 1;
    useLiveDecisions.setState((s) => {
      if (!(key in s.records)) return s;
      const records = { ...s.records };
      delete records[key];
      return { records };
    });
  },
}));
