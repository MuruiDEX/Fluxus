"use client";

import { Pause, Play, RotateCcw, Square, StepForward } from "lucide-react";
import { pauseLoop, resetScope, resumeLoop, startLoop, stopLoop, tickOnce, useEngine } from "@/lib/sim/engine";
import type { Scope } from "@/lib/sim/core";
import { cn } from "@/lib/cn";

export function EngineControls({ scope, compact }: { scope: Scope; compact?: boolean }) {
  const { running, paused, tickCount } = useEngine();

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span
        className={cn(
          "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-semibold",
          running
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
            : "bg-mist-100 text-ink-500 dark:bg-white/10 dark:text-slate-300",
        )}
      >
        <span className={cn("h-1.5 w-1.5 rounded-full", running ? "animate-pulse-dot bg-emerald-500" : "bg-current")} />
        {running ? `Executing · tick ${tickCount}` : paused ? `Paused · tick ${tickCount}` : "Idle"}
      </span>
      {!running && !paused && (
        <button
          onClick={() => startLoop(scope)}
          className="flex h-8 items-center gap-1.5 rounded-lg bg-gradient-to-r from-brand-500 to-sky-400 px-3 text-[12.5px] font-semibold text-white hover:opacity-90"
        >
          <Play className="h-3.5 w-3.5" /> {compact ? "Run" : "Start execution"}
        </button>
      )}
      {running && (
        <button
          onClick={() => pauseLoop()}
          className="flex h-8 items-center gap-1.5 rounded-lg border border-mist-200 bg-white px-3 text-[12.5px] font-medium hover:bg-mist-100 dark:border-white/10 dark:bg-white/5"
        >
          <Pause className="h-3.5 w-3.5" /> Pause
        </button>
      )}
      {paused && (
        <button
          onClick={() => resumeLoop()}
          className="flex h-8 items-center gap-1.5 rounded-lg bg-gradient-to-r from-brand-500 to-sky-400 px-3 text-[12.5px] font-semibold text-white hover:opacity-90"
        >
          <Play className="h-3.5 w-3.5" /> Resume
        </button>
      )}
      {(running || paused) && (
        <button
          onClick={() => stopLoop()}
          className="flex h-8 items-center gap-1.5 rounded-lg border border-mist-200 bg-white px-3 text-[12.5px] font-medium hover:bg-mist-100 dark:border-white/10 dark:bg-white/5"
        >
          <Square className="h-3.5 w-3.5" /> Stop
        </button>
      )}
      <button
        onClick={() => resetScope(scope)}
        className="flex h-8 items-center gap-1.5 rounded-lg border border-mist-200 bg-white px-3 text-[12.5px] font-medium hover:bg-mist-100 dark:border-white/10 dark:bg-white/5"
      >
        <RotateCcw className="h-3.5 w-3.5" /> Reset
      </button>
      {!running && (
        <button
          onClick={() => tickOnce(scope)}
          title="Advance exactly one deterministic tick"
          className="flex h-8 items-center gap-1.5 rounded-lg border border-mist-200 bg-white px-3 text-[12.5px] font-medium hover:bg-mist-100 dark:border-white/10 dark:bg-white/5"
        >
          <StepForward className="h-3.5 w-3.5" /> Step
        </button>
      )}
    </div>
  );
}
