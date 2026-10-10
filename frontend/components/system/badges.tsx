import { cn } from "@/lib/cn";
import type { AgentStatus, TaskStatus } from "@/lib/domain/types";

const agentStyles: Record<AgentStatus, string> = {
  available: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  thinking: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  working: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-sky-300",
  waiting_dependency: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  waiting_approval: "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300",
  reviewing: "bg-cyan-100 text-cyan-700 dark:bg-cyan-500/15 dark:text-cyan-300",
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300",
  escalated: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300",
};

const agentLabels: Record<AgentStatus, string> = {
  available: "Available",
  thinking: "Thinking",
  working: "Working",
  waiting_dependency: "Waiting for dependency",
  waiting_approval: "Waiting for approval",
  reviewing: "Reviewing",
  completed: "Completed",
  failed: "Failed",
  escalated: "Escalated",
};

const taskStyles: Record<TaskStatus, string> = {
  not_started: "bg-slate-100 text-slate-600 dark:bg-slate-500/15 dark:text-slate-300",
  working: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-sky-300",
  waiting_dependency: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  waiting_assets: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  waiting_approval: "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300",
  completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300",
};

const taskLabels: Record<TaskStatus, string> = {
  not_started: "Not started",
  working: "Working",
  waiting_dependency: "Waiting for dependency",
  waiting_assets: "Waiting for assets",
  waiting_approval: "Waiting for approval",
  completed: "Completed",
  failed: "Failed",
};

export function AgentStatusBadge({ status, live }: { status: AgentStatus; live?: boolean }) {
  const active = status === "working" || status === "thinking";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        agentStyles[status],
      )}
    >
      {live && active && <span className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-current" />}
      {agentLabels[status]}
    </span>
  );
}

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        taskStyles[status],
      )}
    >
      {taskLabels[status]}
    </span>
  );
}

export function Avatar({ name, hue, size = 28 }: { name: string; hue: number; size?: number }) {
  const initials = name
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.36,
        background: `linear-gradient(135deg, hsl(${hue} 70% 52%), hsl(${(hue + 40) % 360} 70% 44%))`,
      }}
    >
      {initials}
    </span>
  );
}

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-mist-200 dark:bg-white/10", className)}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-brand-500 to-sky-400 transition-all duration-700"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}
