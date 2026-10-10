import type { ReactNode } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";
import { cn } from "@/lib/cn";

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
        <h1 className="flex flex-wrap items-center gap-2 text-[20px] font-bold leading-7 text-ink-950 dark:text-white">
          {title}
          {badge}
        </h1>
        {sub && <p className="mt-0.5 text-[13px] text-ink-500 dark:text-slate-400">{sub}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function DemoBadge() {
  return (
    <span
      title="Demo business: figures and AI activity are simulated, not real business activity or integrations."
      className="rounded-full border border-dashed border-brand-200 bg-brand-50 px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-wide text-brand-600 dark:border-brand-500/40 dark:bg-brand-500/10 dark:text-sky-300"
    >
      Demo · simulated
    </span>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-mist-200 bg-white shadow-card dark:border-white/10 dark:bg-night-900",
        className,
      )}
    >
      {children}
    </div>
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
  const good = invert ? delta < 0 : delta > 0;
  return (
    <Card className="p-4">
      <p className="text-[12px] font-medium text-ink-500 dark:text-slate-400">{label}</p>
      <div className="mt-1 flex items-baseline gap-2">
        <p className="text-[22px] font-bold tabular-nums text-ink-950 dark:text-white">{value}</p>
        <span
          className={cn(
            "flex items-center gap-0.5 text-[12px] font-semibold tabular-nums",
            good ? "text-emerald-600" : "text-red-500",
          )}
        >
          {delta > 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
          {delta > 0 ? "+" : ""}
          {delta}%
        </span>
      </div>
      {spark && (
        <svg viewBox="0 0 100 28" className="mt-2 h-7 w-full" aria-hidden>
          <polyline
            points={spark.map((v, i) => `${(i / (spark.length - 1)) * 100},${28 - (v / Math.max(...spark)) * 24}`).join(" ")}
            fill="none"
            stroke={good ? "#16a34a" : "#ef4444"}
            strokeOpacity="0.35"
            strokeWidth="2"
          />
          <polygon
            points={`0,28 ${spark.map((v, i) => `${(i / (spark.length - 1)) * 100},${28 - (v / Math.max(...spark)) * 24}`).join(" ")} 100,28`}
            fill={good ? "#16a34a" : "#ef4444"}
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
      <p className="text-[15px] font-semibold text-ink-950 dark:text-white">{title}</p>
      <p className="max-w-sm text-[13px] text-ink-500 dark:text-slate-400">{body}</p>
      {action && <div className="mt-3">{action}</div>}
    </Card>
  );
}

export function PrimaryButton({
  children,
  onClick,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-[10px] bg-gradient-to-r from-brand-500 to-sky-400 px-3.5 text-[13px] font-semibold text-white shadow-card transition-opacity hover:opacity-90",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function GhostButton({
  children,
  onClick,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-mist-200 bg-white px-3.5 text-[13px] font-medium text-ink-700 transition-colors hover:bg-mist-100 dark:border-white/10 dark:bg-white/5 dark:text-slate-200 dark:hover:bg-white/10",
        className,
      )}
    >
      {children}
    </button>
  );
}
