"use client";

import { Check, Loader2, X } from "lucide-react";
import { useLiveDecisions, type ApprovalDecision, type Decision } from "@/lib/store/decisions";
import { GhostButton, PrimaryButton } from "@/components/system/ui";
import { cn } from "@/lib/cn";

export type DecisionTone = "ok" | "bad" | "warn" | "info";

export interface DecisionView {
  /** Which control set to render. */
  kind: "idle" | "deciding" | "decided" | "error";
  message: string | null;
  tone: DecisionTone;
  showRetry: boolean;
}

/**
 * Pure presentation mapping for a decision record (unit-tested; the thin
 * component below only renders it — React Server rendering cannot observe
 * client store state, so branches are pinned here instead of via SSR).
 */
export function describeDecision(record: ApprovalDecision | undefined): DecisionView {
  const phase = record?.phase ?? "idle";
  if (phase === "deciding") {
    return { kind: "deciding", message: null, tone: "info", showRetry: false };
  }
  if (phase === "error") {
    return {
      kind: "error",
      message: record?.message ?? "Request failed.",
      tone: "bad",
      showRetry: true,
    };
  }
  if (phase === "decided") {
    const tone: DecisionTone = record?.errorCode ? "warn" : "ok";
    return { kind: "decided", message: record?.message ?? null, tone, showRetry: false };
  }
  return { kind: "idle", message: null, tone: "info", showRetry: false };
}

/**
 * LIVE per-approval decision control (Stage 7.7). Rendered only in LIVE
 * branches for pending approvals — never in DEMO (the runner itself also
 * refuses non-member contexts).
 *
 * - One POST per generation: disabled while deciding + store-level flight
 *   dedupe survive double clicks, StrictMode, and remounts.
 * - No optimistic writes: the row flips only after the server answers.
 * - No task execution here: deciding never runs the linked task.
 */
export function DecisionButtons({
  businessId,
  approvalId,
}: {
  businessId: string;
  approvalId: string;
}) {
  const key = `${businessId}/${approvalId}`;
  const record = useLiveDecisions((s) => s.records[key]);
  const decide = useLiveDecisions((s) => s.decide);
  const view = describeDecision(record);

  const act = (decision: Decision) => void decide(businessId, approvalId, decision);

  if (view.kind === "deciding") {
    return (
      <div className="mt-3 flex items-center gap-2">
        <span className="flex h-9 items-center gap-1.5 rounded-[10px] bg-brand-500/10 px-3.5 text-[13px] font-semibold text-brand-600 dark:text-sky-300">
          <Loader2 className="h-4 w-4 animate-spin" /> Deciding…
        </span>
      </div>
    );
  }

  if (view.kind === "decided" || view.kind === "error") {
    return (
      <div className="mt-3 flex flex-col gap-2">
        {view.message && (
          <p
            className={cn(
              "text-[12.5px] leading-5",
              view.tone === "ok" && "text-emerald-700 dark:text-emerald-300",
              view.tone === "bad" && "text-red-600 dark:text-red-300",
              view.tone === "warn" && "text-amber-700 dark:text-amber-300",
              view.tone === "info" && "text-ink-500 dark:text-slate-400",
            )}
          >
            {view.message}
          </p>
        )}
        {view.showRetry && (
          <div className="flex gap-2">
            <PrimaryButton onClick={() => act("approved")}>Retry approve</PrimaryButton>
            <GhostButton onClick={() => act("rejected")}>Retry reject</GhostButton>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <PrimaryButton onClick={() => act("approved")}>
        <Check className="h-4 w-4" /> Approve
      </PrimaryButton>
      <GhostButton onClick={() => act("rejected")}>
        <X className="h-4 w-4" /> Reject
      </GhostButton>
    </div>
  );
}
