"use client";

/**
 * LIVE brief-intake runner (Stage 11) — drives the existing backend
 * `POST .../briefs`, `POST .../briefs/{id}/answers` and
 * `POST .../briefs/{id}/plan` endpoints through goal → questions →
 * answers → brief review → plan.
 *
 * Safety rules (all enforced here, never trusted to the caller):
 * - DEMO ban: runs only with an authenticated session whose membership
 *   list contains the target business. Anything else records an error and
 *   sends nothing.
 * - Single flight per intake: concurrent calls (double click, StrictMode,
 *   remount) share one promise — exactly one request per generation.
 * - No optimistic state: brief rows and the project graph appear only from
 *   2xx answers, mapped verbatim. Status transitions are server-driven
 *   (questions_ready → brief_ready → planned); the client never invents
 *   statuses, questions, or plans.
 * - Honest errors: 401 flows through the shared session handler (caller
 *   falls back to DEMO) and still resolves the record as error — forms never
 *   hang in flight. 409/422 surface the server message with retry; network
 *   loss (status 0) is reported as unknown-outcome, never success.
 * - Resume: any brief id reloads from GET .../briefs/{id} (server truth),
 *   so a page reload with ?brief= continues where the server left off.
 */

import { create } from "zustand";
import {
  ApiError,
  apiAnswerBrief,
  apiCreateBrief,
  apiGetBrief,
  apiPlanBrief,
  isAbortError,
  type ApiBrief,
  type ApiBriefAnswer,
  type ApiPlan,
} from "@/lib/api";
import { useSession } from "@/lib/store/auth";

export type IntakePhase =
  | "idle"
  | "asking"
  | "answering"
  | "submitting"
  | "reviewing"
  | "planning"
  | "done"
  | "error";

export interface IntakeRecord {
  phase: IntakePhase;
  brief: ApiBrief | null;
  plan: ApiPlan | null;
  message: string | null;
  errorCode: string | null;
  updatedAt: number;
}

interface IntakeState {
  records: Record<string, IntakeRecord>;
  start: (businessId: string, goal: string) => Promise<void>;
  answer: (businessId: string, briefId: string, answers: ApiBriefAnswer[]) => Promise<void>;
  plan: (businessId: string, briefId: string) => Promise<void>;
  resume: (businessId: string, briefId: string) => Promise<void>;
  clear: (businessId: string) => void;
}

const idleRecord: IntakeRecord = {
  phase: "idle",
  brief: null,
  plan: null,
  message: null,
  errorCode: null,
  updatedAt: 0,
};

const flights: Record<string, Promise<void> | undefined> = {};
const seqs: Record<string, number> = {};

function setRecord(businessId: string, patch: Partial<IntakeRecord>): void {
  useLiveIntake.setState((s) => ({
    records: {
      ...s.records,
      [businessId]: { ...s.records[businessId], ...idleRecord, ...patch, updatedAt: Date.now() },
    },
  }));
}

function gateError(businessId: string): { message: string; errorCode: string } | null {
  const session = useSession.getState();
  if (session.status !== "authenticated") {
    return { message: "Sign in to start intake.", errorCode: "unauthorized" };
  }
  if (!session.businesses.includes(businessId)) {
    return { message: "No access to this business.", errorCode: "forbidden" };
  }
  return null;
}

function finishError(businessId: string, seq: number, err: unknown): void {
  const alive = () => seqs[`intake/${businessId}`] === seq;
  if (!alive() || isAbortError(err)) return;
  if (!(err instanceof ApiError)) {
    setRecord(businessId, {
      phase: "error",
      message: "Unexpected client error.",
      errorCode: null,
    });
    return;
  }
  if (err.status === 0) {
    setRecord(businessId, {
      phase: "error",
      message: "Lost contact with the server — nothing was saved. Retry to reconcile.",
      errorCode: err.code,
    });
    return;
  }
  // 401 already cleared CSRF and notified the session layer (DEMO fallback);
  // the record still resolves as error so the form leaves its flight phase.
  setRecord(businessId, { phase: "error", message: err.message, errorCode: err.code });
}

function runIntake(
  businessId: string,
  work: (seq: number) => Promise<void>,
): Promise<void> {
  const flightKey = `intake/${businessId}`;
  const flight = flights[flightKey];
  if (flight) return flight;
  const seq = (seqs[flightKey] ?? 0) + 1;
  seqs[flightKey] = seq;
  const promise = work(seq).finally(() => {
    if (flights[flightKey] === promise) delete flights[flightKey];
  });
  flights[flightKey] = promise;
  return promise;
}

async function startWork(businessId: string, goal: string, seq: number): Promise<void> {
  const alive = () => seqs[`intake/${businessId}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord(businessId, { phase: "error", ...gated });
    return;
  }
  if (!goal.trim()) {
    setRecord(businessId, {
      phase: "error",
      message: "Describe the goal first.",
      errorCode: "validation_error",
    });
    return;
  }
  setRecord(businessId, { phase: "asking", brief: null, plan: null, message: null, errorCode: null });
  let brief: ApiBrief;
  try {
    brief = await apiCreateBrief(businessId, goal.trim());
  } catch (err) {
    finishError(businessId, seq, err);
    return;
  }
  if (!alive()) return;
  setRecord(businessId, {
    phase: "answering",
    brief,
    message: null,
    errorCode: null,
  });
}

async function answerWork(
  businessId: string,
  briefId: string,
  answers: ApiBriefAnswer[],
  seq: number,
): Promise<void> {
  const alive = () => seqs[`intake/${businessId}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord(businessId, { phase: "error", ...gated });
    return;
  }
  setRecord(businessId, { phase: "submitting", message: null, errorCode: null });
  let brief: ApiBrief;
  try {
    brief = await apiAnswerBrief(businessId, briefId, answers);
  } catch (err) {
    finishError(businessId, seq, err);
    return;
  }
  if (!alive()) return;
  setRecord(businessId, {
    phase: "reviewing",
    brief,
    message: null,
    errorCode: null,
  });
}

async function planWork(
  businessId: string,
  briefId: string,
  seq: number,
): Promise<void> {
  const alive = () => seqs[`intake/${businessId}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord(businessId, { phase: "error", ...gated });
    return;
  }
  setRecord(businessId, { phase: "planning", message: null, errorCode: null });
  let plan: ApiPlan;
  try {
    plan = await apiPlanBrief(businessId, briefId);
  } catch (err) {
    finishError(businessId, seq, err);
    return;
  }
  if (!alive()) return;
  let brief: ApiBrief | null = useLiveIntake.getState().records[businessId]?.brief ?? null;
  try {
    brief = await apiGetBrief(businessId, briefId);
  } catch {
    // Best effort: the plan below already carries the authoritative graph;
    // the brief row refreshes on the next load.
  }
  if (!alive()) return;
  setRecord(businessId, {
    phase: "done",
    brief,
    plan,
    message: "Planned — server confirmed.",
    errorCode: null,
  });
}

async function resumeWork(businessId: string, briefId: string, seq: number): Promise<void> {
  const alive = () => seqs[`intake/${businessId}`] === seq;
  const gated = gateError(businessId);
  if (gated) {
    setRecord(businessId, { phase: "error", ...gated });
    return;
  }
  setRecord(businessId, {
    phase: "submitting",
    brief: null,
    plan: null,
    message: null,
    errorCode: null,
  });
  let brief: ApiBrief;
  try {
    brief = await apiGetBrief(businessId, briefId);
  } catch (err) {
    finishError(businessId, seq, err);
    return;
  }
  if (!alive()) return;
  // Server status decides the resumed step — never the URL or memory.
  const phase =
    brief.status === "questions_ready"
      ? ("answering" as const)
      : brief.status === "brief_ready"
        ? ("reviewing" as const)
        : brief.status === "planned" || brief.status === "running" || brief.status === "completed"
          ? ("done" as const)
          : ("error" as const);
  if (phase === "error") {
    setRecord(businessId, {
      phase: "error",
      brief,
      message: `Brief is ${brief.status} — intake cannot continue it.`,
      errorCode: "conflict",
    });
    return;
  }
  setRecord(businessId, { phase, brief, message: null, errorCode: null });
}

export const useLiveIntake = create<IntakeState>(() => ({
  records: {},
  start: (businessId, goal) => runIntake(businessId, (seq) => startWork(businessId, goal, seq)),
  answer: (businessId, briefId, answers) =>
    runIntake(businessId, (seq) => answerWork(businessId, briefId, answers, seq)),
  plan: (businessId, briefId) => runIntake(businessId, (seq) => planWork(businessId, briefId, seq)),
  resume: (businessId, briefId) =>
    runIntake(businessId, (seq) => resumeWork(businessId, briefId, seq)),
  clear: (businessId) => {
    seqs[`intake/${businessId}`] = (seqs[`intake/${businessId}`] ?? 0) + 1;
    useLiveIntake.setState((s) => {
      if (!(businessId in s.records)) return s;
      const records = { ...s.records };
      delete records[businessId];
      return { records };
    });
  },
}));
