"use client";

import { Check, Loader2 } from "lucide-react";
import { useLiveWrites, type WriteRecord } from "@/lib/store/writes";
import { PrimaryButton } from "@/components/system/ui";
import { cn } from "@/lib/cn";

export type HireTone = "ok" | "bad" | "warn" | "info";

export interface HireView {
  /** Which control set to render. */
  kind: "idle" | "hiring" | "hired" | "error";
  message: string | null;
  tone: HireTone;
  showRetry: boolean;
}

/**
 * Pure presentation mapping for a hire record (unit-tested; the thin
 * component below only renders it — React Server rendering cannot observe
 * client store state, so branches are pinned here instead of via SSR).
 */
export function describeHire(record: WriteRecord | undefined, hired: boolean): HireView {
  if (hired) {
    return { kind: "hired", message: null, tone: "ok", showRetry: false };
  }
  const phase = record?.phase ?? "idle";
  if (phase === "saving") {
    return { kind: "hiring", message: null, tone: "info", showRetry: false };
  }
  if (phase === "error") {
    return {
      kind: "error",
      message: record?.message ?? "Request failed.",
      tone: "bad",
      showRetry: true,
    };
  }
  if (phase === "saved") {
    return { kind: "hired", message: record?.message ?? null, tone: "ok", showRetry: false };
  }
  return { kind: "idle", message: null, tone: "info", showRetry: false };
}

/**
 * LIVE hire control (Stage 8.3). Rendered only in LIVE marketplace branches
 * for hireable listings — never in DEMO (the runner itself also refuses
 * non-member contexts).
 *
 * - One POST per generation: disabled while hiring + store-level flight
 *   dedupe survive double clicks, StrictMode, and remounts.
 * - No optimistic writes: Hired flips only after the server answers 201
 *   (or the list reload confirms it after a 409 race).
 * - Pack listings never render this button: the detail page explains that
 *   packs install capabilities instead of hiring an agent.
 */
export function HireButton({
  businessId,
  slug,
  hired,
  price,
}: {
  businessId: string;
  slug: string;
  hired: boolean;
  price: string;
}) {
  const key = `${businessId}/${slug}`;
  const record = useLiveWrites((s) => s.hires[key]);
  const hireAgent = useLiveWrites((s) => s.hireAgent);
  const view = describeHire(record, hired);

  const act = () => void hireAgent(businessId, slug);

  if (view.kind === "hired") {
    return (
      <div className="flex flex-col gap-1">
        <p className="flex items-center gap-1.5 text-[12.5px] font-bold text-emerald-600">
          <Check className="h-4 w-4" /> On your team
        </p>
        {view.message && (
          <p className="text-[12px] text-emerald-700 dark:text-emerald-300">{view.message}</p>
        )}
      </div>
    );
  }

  if (view.kind === "hiring") {
    return (
      <span className="flex h-8 items-center gap-1.5 rounded-lg bg-brand-500/10 px-3.5 text-[12.5px] font-semibold text-brand-600 dark:text-sky-300">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Hiring…
      </span>
    );
  }

  if (view.kind === "error") {
    return (
      <div className="flex flex-col gap-1.5">
        {view.message && (
          <p role="alert" className="text-[12px] leading-5 text-red-600 dark:text-red-300">
            {view.message}
          </p>
        )}
        <div>
          <PrimaryButton onClick={act}>Retry hire</PrimaryButton>
        </div>
      </div>
    );
  }

  return (
    <button
      onClick={act}
      className={cn(
        "h-8 rounded-lg bg-brand-500 px-3.5 text-[12.5px] font-semibold text-white hover:opacity-90",
      )}
    >
      Hire — {price}
    </button>
  );
}

export type PackInstallKind = "idle" | "installing" | "installed" | "error";

export interface PackInstallView {
  kind: PackInstallKind;
  message: string | null;
}

/** Pure mapping for a pack install record (mirrors describeHire). */
export function describePackInstall(
  record: WriteRecord | undefined,
  installed: boolean,
): PackInstallView {
  if (installed) {
    return { kind: "installed", message: null };
  }
  const phase = record?.phase ?? "idle";
  if (phase === "saving") return { kind: "installing", message: null };
  if (phase === "error") {
    return { kind: "error", message: record?.message ?? "Install failed." };
  }
  if (phase === "saved") return { kind: "installed", message: record?.message ?? null };
  return { kind: "idle", message: null };
}

/**
 * LIVE pack install control (Stage 15). One POST per generation, no optimistic
 * state: Installed flips only after the server receipt confirms it. Concurrent
 * installs share the store-level flight, so two clicks (or two tabs) can never
 * create two receipts.
 */
export function PackInstallButton({
  businessId,
  slug,
  installed,
  price,
}: {
  businessId: string;
  slug: string;
  installed: boolean;
  price?: string;
}) {
  const key = `${businessId}/${slug}`;
  const record = useLiveWrites((s) => s.hires[key]);
  const installPack = useLiveWrites((s) => s.installPack);
  const view = describePackInstall(record, installed);
  const act = () => void installPack(businessId, slug);

  if (view.kind === "installed") {
    return (
      <div className="flex flex-col gap-1">
        <p className="flex items-center gap-1.5 text-[12.5px] font-bold text-emerald-600">
          <Check className="h-4 w-4" /> Installed
        </p>
        {view.message && (
          <p className="text-[12px] text-emerald-700 dark:text-emerald-300">{view.message}</p>
        )}
      </div>
    );
  }
  if (view.kind === "installing") {
    return (
      <span className="flex h-8 items-center gap-1.5 rounded-lg bg-brand-500/10 px-3.5 text-[12.5px] font-semibold text-brand-600 dark:text-sky-300">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Installing…
      </span>
    );
  }
  if (view.kind === "error") {
    return (
      <div className="flex flex-col gap-1.5">
        {view.message && (
          <p role="alert" className="text-[12px] leading-5 text-red-600 dark:text-red-300">
            {view.message}
          </p>
        )}
        <div>
          <PrimaryButton onClick={act}>Retry install</PrimaryButton>
        </div>
      </div>
    );
  }
  return (
    <button
      onClick={act}
      className="h-8 rounded-lg bg-brand-500 px-3.5 text-[12.5px] font-semibold text-white hover:opacity-90"
    >
      Install Pack{price ? ` — ${price}` : ""}
    </button>
  );
}
