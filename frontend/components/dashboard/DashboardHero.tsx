"use client";

import Link from "next/link";
import { ArrowRight, Bot, Plus, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import { FluxusOrbital } from "@/components/system/FluxusMark";
import { buttonClass } from "@/components/system/controls";
import { cn } from "@/lib/cn";

/**
 * Dashboard hero: the Fluxus identity, the owner's welcome and the live
 * system state in one glass slab. Every value comes from the dashboard's
 * already-loaded stores; the orbital ornament is decorative only.
 */
export function DashboardHero({
  mode,
  greetingName,
  businessName,
  systemLabel,
  systemTone = "success",
  activeAgents,
  pendingApprovals,
  onOpenCommand,
}: {
  mode: "demo" | "live";
  greetingName: string;
  businessName?: string;
  systemLabel: string;
  systemTone?: "success" | "warning" | "danger" | "brand";
  activeAgents: number;
  pendingApprovals: number;
  onOpenCommand: () => void;
}) {
  const toneClass = {
    danger: "border-danger-line bg-danger-soft text-danger-fg",
    warning: "border-warning-line bg-warning-soft text-warning-fg",
    brand: "border-brand/25 bg-brand-soft text-brand",
    success: "border-success-line bg-success-soft text-success-fg",
  }[systemTone];

  return (
    <section
      aria-labelledby="dashboard-title"
      className="flux-hero relative rounded-card p-5 sm:p-6 lg:p-7"
    >
      <span aria-hidden="true" className="flux-scan" />
      <div
        aria-hidden="true"
        className="flux-grid-overlay pointer-events-none absolute inset-0 opacity-50"
      />

      <div className="relative grid h-full items-stretch gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(280px,380px)]">
        <div className="flex min-w-0 flex-col justify-center">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-brand/20 bg-surface/60 px-2.5 py-1 text-micro font-semibold uppercase tracking-[0.14em] text-brand backdrop-blur">
              <Sparkles aria-hidden="true" className="h-3 w-3" />
              AI Business OS
            </span>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-micro font-semibold",
                toneClass,
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "h-1.5 w-1.5 rounded-full bg-current",
                  systemTone !== "danger" && "animate-pulse-dot",
                )}
              />
              {systemLabel}
            </span>
          </div>

          <h1 id="dashboard-title" className="mt-4">
            <span className="flux-hero-title flux-holo-text block">Fluxus.</span>
            <span className="flux-hero-lead mt-2 block font-semibold text-fg">
              Welcome back, {greetingName}.
            </span>
          </h1>
          <p className="mt-2 max-w-xl text-body-sm leading-6 text-fg-muted">
            {businessName ? (
              <>
                <span className="font-semibold text-fg">{businessName}</span>{" "}
                {mode === "live" ? "is running on server data" : "is running on a simulated demo dataset"}
                . Your AI team is ready to execute your vision.
              </>
            ) : (
              "Your AI team is ready to execute your vision."
            )}
          </p>

          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Link
              href="/projects/new"
              className={buttonClass({
                variant: "primary",
                size: "lg",
                className: "rounded-full px-5",
              })}
            >
              <Plus aria-hidden="true" className="h-4 w-4" />
              Create Task
              <ArrowRight aria-hidden="true" className="h-4 w-4" />
            </Link>
            <a
              href="#agent-workflow"
              className={buttonClass({
                variant: "secondary",
                size: "lg",
                className: "rounded-full bg-surface/60 backdrop-blur",
              })}
            >
              <Workflow aria-hidden="true" className="h-4 w-4" />
              View workflow
            </a>
            <button
              type="button"
              onClick={onOpenCommand}
              className={buttonClass({ variant: "ghost", size: "lg", className: "rounded-full" })}
            >
              <Sparkles aria-hidden="true" className="h-4 w-4 text-brand" />
              Ask Fluxus
              <kbd className="ml-1 rounded-md border border-line bg-surface/70 px-1.5 py-0.5 text-micro font-semibold text-fg-subtle">
                Ctrl K
              </kbd>
            </button>
          </div>

          <dl className="mt-6 flex flex-wrap gap-2">
            <div className="flex items-center gap-2 rounded-control border border-line/70 bg-surface/55 px-3 py-1.5 backdrop-blur">
              <dt className="text-caption text-fg-subtle">Active agents</dt>
              <dd className="inline-flex items-center gap-1 text-caption font-bold tabular-nums text-fg">
                <Bot aria-hidden="true" className="h-3.5 w-3.5 text-brand" />
                {activeAgents}
              </dd>
            </div>
            <div className="flex items-center gap-2 rounded-control border border-line/70 bg-surface/55 px-3 py-1.5 backdrop-blur">
              <dt className="text-caption text-fg-subtle">Approvals</dt>
              <dd className="text-caption font-bold tabular-nums text-fg">{pendingApprovals}</dd>
            </div>
            <div className="flex items-center gap-2 rounded-control border border-line/70 bg-surface/55 px-3 py-1.5 backdrop-blur">
              <dt className="text-caption text-fg-subtle">Source</dt>
              <dd className="text-caption font-bold text-fg">{mode === "live" ? "Server" : "Demo"}</dd>
            </div>
          </dl>
        </div>

        <div className="relative mx-auto hidden h-[236px] w-full max-w-[360px] self-center sm:block lg:h-[300px]">
          <span aria-hidden="true" className="flux-aurora absolute inset-6 rounded-full" />
          <FluxusOrbital className="h-full w-full" />
          <div className="flux-glass absolute bottom-0 left-1/2 w-[88%] -translate-x-1/2 rounded-control px-3 py-2 shadow-card">
            <div className="flex items-center justify-between gap-2">
              <span className="inline-flex items-center gap-1.5 text-micro font-semibold uppercase tracking-[0.12em] text-fg-subtle">
                <ShieldCheck aria-hidden="true" className="h-3 w-3 text-brand" />
                Fluxus v2.0
              </span>
              <span className="inline-flex items-center gap-1 text-micro font-semibold text-success-fg">
                <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
                {mode === "live" ? "Connected" : "Simulation ready"}
              </span>
            </div>
            <div className="mt-1.5 flex items-end justify-between gap-3">
              <p className="text-micro text-fg-subtle">
                {mode === "live" ? "Enterprise edition · live workspace" : "Enterprise edition · demo workspace"}
              </p>
              <span aria-hidden="true" className="flex items-end gap-0.5">
                {[38, 62, 48, 78, 58, 92].map((height, index) => (
                  <span
                    key={index}
                    className="w-1 rounded-full bg-gradient-to-t from-brand/30 to-accent"
                    style={{ height: (height / 100) * 18 }}
                  />
                ))}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
