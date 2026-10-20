"use client";

import { Check, Loader2, Pause } from "lucide-react";
import { useLiveWrites, type WriteRecord } from "@/lib/store/writes";
import { GhostButton, PrimaryButton } from "@/components/system/ui";
import { cn } from "@/lib/cn";

export type AgentToggleTone = "ok" | "bad" | "warn" | "info";

export interface AgentToggleView {
  /** Which control set to render. */
  kind: "active" | "disabled" | "saving" | "error";
  message: string | null;
  tone: AgentToggleTone;
  showRetry: boolean;
}

/**
 * Pure presentation mapping for an agent lifecycle record (unit-tested; the
 * thin component below only renders it — React Server rendering cannot
 * observe client store state, so branches are pinned here instead of via
 * SSR). `hired` is the server row; the in-flight record only overrides it
 * while saving or on error.
 */
export function describeAgentToggle(
  record: WriteRecord | undefined,
  hired: boolean,
): AgentToggleView {
  const phase = record?.phase ?? "idle";
  if (phase === "saving") {
    return { kind: "saving", message: null, tone: "info", showRetry: false };
  }
  if (phase === "error") {
    return {
      kind: "error",
      message: record?.message ?? "Request failed.",
      tone: "bad",
      showRetry: true,
    };
  }
  if (hired) {
    return { kind: "active", message: record?.message ?? null, tone: "ok", showRetry: false };
  }
  return { kind: "disabled", message: record?.message ?? null, tone: "warn", showRetry: false };
}

/**
 * LIVE per-agent lifecycle control (Stage 14). Rendered only in LIVE
 * branches — never in DEMO (the runner itself also refuses non-member
 * contexts).
 *
 * - One POST per generation: disabled while saving + store-level flight
 *   dedupe survive double clicks, StrictMode, and remounts.
 * - No optimistic writes: the flag flips only after the server answers.
 * - Disabling never deletes history: tasks, reports and audit events stay.
 */
export function AgentToggleButton({
  businessId,
  agentId,
  hired,
}: {
  businessId: string;
  agentId: string;
  hired: boolean;
}) {
  const key = `${businessId}/${agentId}`;
  const record = useLiveWrites((s) => s.agents[key]);
  const activateAgent = useLiveWrites((s) => s.activateAgent);
  const disableAgent = useLiveWrites((s) => s.disableAgent);
  const view = describeAgentToggle(record, hired);

  if (view.kind === "saving") {
    return (
      <span className="flex h-7 items-center gap-1.5 rounded-lg bg-brand-500/10 px-2.5 text-[12px] font-semibold text-brand-600 dark:text-sky-300">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving…
      </span>
    );
  }

  if (view.kind === "error") {
    return (
      <div className="flex flex-col gap-1.5">
        {view.message && (
          <p
            role="alert"
            className="text-[11.5px] leading-5 text-red-600 dark:text-red-300"
          >
            {view.message}
          </p>
        )}
        <div>
          <PrimaryButton
            onClick={() =>
              void (hired
                ? disableAgent(businessId, agentId)
                : activateAgent(businessId, agentId))
            }
          >
            Retry
          </PrimaryButton>
        </div>
      </div>
    );
  }

  if (view.kind === "disabled") {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-[11.5px] font-medium text-amber-700 dark:text-amber-300">
          Disabled — takes no new work.
        </p>
        <div className="flex gap-2">
          <PrimaryButton onClick={() => void activateAgent(businessId, agentId)}>
            <Check className="h-4 w-4" /> Activate
          </PrimaryButton>
        </div>
        {view.message && (
          <p className="text-[11.5px] text-emerald-700 dark:text-emerald-300">{view.message}</p>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-2">
        <GhostButton onClick={() => void disableAgent(businessId, agentId)}>
          <Pause className="h-4 w-4" /> Disable
        </GhostButton>
      </div>
      {view.message && (
        <p
          className={cn(
            "text-[11.5px]",
            view.tone === "ok" && "text-emerald-700 dark:text-emerald-300",
          )}
        >
          {view.message}
        </p>
      )}
    </div>
  );
}
