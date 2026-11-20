import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { AgentStatus, TaskStatus } from "@/lib/domain/types";

/**
 * Generic semantic badge.
 *
 * Tones are the only status palette the app needs: every colour resolves
 * through the Slice 1 semantic tokens, so light and dark mode stay in one
 * definition. `xs` is the micro uppercase pill (environment badges) and `sm`
 * is the label-size status pill used by the domain badges below.
 */
export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info" | "brand";
export type BadgeSize = "xs" | "sm";

const badgeTones: Record<BadgeTone, string> = {
  neutral: "border-line bg-surface-sunken text-fg-muted",
  success: "border-success-line bg-success-soft text-success-fg",
  warning: "border-warning-line bg-warning-soft text-warning-fg",
  danger: "border-danger-line bg-danger-soft text-danger-fg",
  info: "border-info-line bg-info-soft text-info-fg",
  brand: "border-brand-soft-hover bg-brand-soft text-brand",
};

const badgeSizes: Record<BadgeSize, string> = {
  xs: "px-2 py-0.5 text-micro font-bold uppercase tracking-wide",
  sm: "px-2 py-0.5 text-label font-medium",
};

export function badgeClass({
  tone = "neutral",
  size = "sm",
  className,
}: {
  tone?: BadgeTone;
  size?: BadgeSize;
  className?: string;
} = {}) {
  return cn(
    "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border transition-colors duration-150 motion-reduce:transition-none",
    badgeSizes[size],
    badgeTones[tone],
    className,
  );
}

export function Badge({
  tone = "neutral",
  size = "sm",
  className,
  title,
  style,
  children,
}: {
  tone?: BadgeTone;
  size?: BadgeSize;
  className?: string;
  title?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  return (
    <span title={title} style={style} className={badgeClass({ tone, size, className })}>
      {children}
    </span>
  );
}

/**
 * Domain statuses map onto the shared tones. Labels stay visible, so colour is
 * never the only cue; the mapping only replaces the old raw palette classes.
 */
const agentTones: Record<AgentStatus, BadgeTone> = {
  available: "success",
  thinking: "brand",
  working: "info",
  waiting_dependency: "warning",
  waiting_approval: "warning",
  reviewing: "info",
  completed: "success",
  failed: "danger",
  escalated: "danger",
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

const taskTones: Record<TaskStatus, BadgeTone> = {
  not_started: "neutral",
  working: "info",
  waiting_dependency: "warning",
  waiting_assets: "warning",
  waiting_approval: "warning",
  completed: "success",
  failed: "danger",
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
    <Badge tone={agentTones[status]}>
      {live && active && (
        <span
          aria-hidden="true"
          className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-current motion-reduce:animate-none"
        />
      )}
      {agentLabels[status]}
    </Badge>
  );
}

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return <Badge tone={taskTones[status]}>{taskLabels[status]}</Badge>;
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

/**
 * Progress is rendered with a transform so it never triggers layout. A bar
 * without a label is decoration next to text that already states the value;
 * passing `label` exposes it as a real progressbar to assistive technology.
 */
export function ProgressBar({
  value,
  className,
  label,
}: {
  value: number;
  className?: string;
  label?: string;
}) {
  const clamped = Math.min(100, Math.max(0, value));
  return (
    <div
      role={label ? "progressbar" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      aria-valuemin={label ? 0 : undefined}
      aria-valuemax={label ? 100 : undefined}
      aria-valuenow={label ? Math.round(clamped) : undefined}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken", className)}
    >
      <div
        className="h-full origin-left rounded-full bg-gradient-to-r from-brand-solid via-brand to-accent transition-transform duration-500 motion-reduce:transition-none"
        style={{ transform: `scaleX(${clamped / 100})` }}
      />
    </div>
  );
}
