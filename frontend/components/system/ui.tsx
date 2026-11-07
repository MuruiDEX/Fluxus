import type { ReactNode } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/cn";

/** One visual definition per control so links and buttons never drift apart. */
const primaryClass =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-control bg-gradient-to-r from-brand-500 to-sky-400 px-3.5 text-body-sm font-semibold text-white shadow-card transition-opacity duration-150 hover:opacity-90 active:opacity-80 disabled:cursor-not-allowed disabled:opacity-60";

const ghostClass =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-control border border-line bg-surface px-3.5 text-body-sm font-medium text-ink-700 transition-colors duration-150 hover:bg-mist-100 active:bg-mist-200 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white/5 dark:text-slate-200 dark:hover:bg-white/10";

export function PageHeader({
  title,
  sub,
  badge,
  actions,
}: {
  title: string;
  sub?: string;
  badge?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="flex flex-wrap items-center gap-2 text-heading-md font-bold text-fg">
          {title}
          {badge}
        </h1>
        {sub && <p className="mt-0.5 text-body-sm text-fg-muted">{sub}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function DemoBadge() {
  return (
    <span
      title="Demo business: figures and AI activity are simulated, not real business activity or integrations."
      // The size token owns font-size; leading must follow the surrounding line box.
      style={{ lineHeight: "inherit" }}
      className="rounded-full border border-dashed border-brand-200 bg-brand-50 px-2 py-0.5 text-micro font-bold uppercase tracking-wide text-brand-600 dark:border-brand-500/40 dark:bg-brand-500/10 dark:text-sky-300"
    >
      Demo · simulated
    </span>
  );
}

export function LiveBadge() {
  return (
    <span
      title="Live business: data comes from the connected backend for your selected business."
      // The size token owns font-size; leading must follow the surrounding line box.
      style={{ lineHeight: "inherit" }}
      className="rounded-full border border-emerald-200 bg-success-soft px-2 py-0.5 text-micro font-bold uppercase tracking-wide text-emerald-700 dark:border-emerald-500/40 dark:text-emerald-300"
    >
      Live · server
    </span>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-card border border-line bg-surface shadow-card",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * Loading placeholder with the shape of the content it replaces, so data
 * arrival does not shift the page (CLS) and the wait has a visual signal
 * instead of a bare "Loading…" line.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-lg bg-mist-200/80 dark:bg-white/10", className)}
    />
  );
}

/** Card-shaped skeleton row used by list and grid loading states. */
export function SkeletonCard({ className }: { className?: string }) {
  return (
    <Card className={cn("flex flex-col gap-3 p-4", className)}>
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-3 w-2/3" />
      <Skeleton className="h-3 w-1/2" />
    </Card>
  );
}

export function MetricCard({
  label,
  value,
  delta,
  invert,
  spark,
}: {
  label: string;
  value: string;
  delta: number;
  invert?: boolean;
  spark?: number[];
}) {
  // A flat delta is neither good nor bad: rendering a red "down" arrow with
  // 0% claimed a decline the server never reported.
  const flat = delta === 0;
  const good = invert ? delta < 0 : delta > 0;
  // Sparkline points: ignore gaps/NaN, and skip the chart when there is no
  // usable range (an all-zero series used to render `NaN` SVG coordinates).
  const series = (spark ?? []).filter((v) => Number.isFinite(v));
  const peak = series.length > 0 ? Math.max(...series) : 0;
  const points =
    series.length > 1 && peak > 0
      ? series.map((v, i) => `${(i / (series.length - 1)) * 100},${28 - (v / peak) * 24}`).join(" ")
      : null;
  return (
    <Card className="p-4">
      <p className="text-caption font-medium text-fg-muted">{label}</p>
      <div className="mt-1 flex items-baseline gap-2">
        <p className="text-heading-lg font-bold tabular-nums text-fg">{value}</p>
        <span
          className={cn(
            "flex items-center gap-0.5 text-caption font-semibold tabular-nums",
            flat
              ? "text-ink-400 dark:text-slate-400"
              : good
                ? "text-emerald-600"
                : "text-red-500",
          )}
        >
          {!flat &&
            (delta > 0 ? (
              <TrendingUp className="h-3.5 w-3.5" />
            ) : (
              <TrendingDown className="h-3.5 w-3.5" />
            ))}
          {delta > 0 ? "+" : ""}
          {delta}%
        </span>
      </div>
      {points && (
        <svg viewBox="0 0 100 28" className="mt-2 h-7 w-full" aria-hidden>
          <polyline
            points={points}
            fill="none"
            stroke={flat ? "#64748b" : good ? "#16a34a" : "#ef4444"}
            strokeOpacity="0.35"
            strokeWidth="2"
          />
          <polygon
            points={`0,28 ${points} 100,28`}
            fill={flat ? "#64748b" : good ? "#16a34a" : "#ef4444"}
            fillOpacity="0.08"
          />
        </svg>
      )}
    </Card>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <Card className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <p className="text-body-lg font-semibold text-fg">{title}</p>
      <p className="max-w-sm text-body-sm text-fg-muted">{body}</p>
      {action && <div className="mt-3">{action}</div>}
    </Card>
  );
}

export function PrimaryButton({
  children,
  onClick,
  className,
  type = "button",
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  /** Defaults to "button": a plain button must never submit a form by accident. */
  type?: "button" | "submit";
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(primaryClass, className)}
    >
      {children}
    </button>
  );
}

export function GhostButton({
  children,
  onClick,
  className,
  type = "button",
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  type?: "button" | "submit";
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(ghostClass, className)}
    >
      {children}
    </button>
  );
}

/**
 * Navigation that looks like a primary control. A `<button>` nested inside
 * an `<a>` is invalid markup and gives keyboard/screen-reader users two
 * controls for one action — these render a single anchor instead.
 */
export function PrimaryLink({
  href,
  children,
  className,
  title,
}: {
  href: string;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <Link href={href} title={title} className={cn(primaryClass, className)}>
      {children}
    </Link>
  );
}

export function GhostLink({
  href,
  children,
  className,
}: {
  href: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Link href={href} className={cn(ghostClass, className)}>
      {children}
    </Link>
  );
}
