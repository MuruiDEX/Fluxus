import type { ReactNode } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/system/badges";
import { Button, buttonClass, type ButtonBaseProps } from "@/components/system/controls";

/**
 * Button visuals live in `controls.tsx`; these wrappers keep the historical
 * `PrimaryButton` / `GhostButton` / link API so existing pages are untouched.
 * `GhostButton` keeps its previous bordered-surface look through the canonical
 * `secondary` variant; the `ghost` variant is the lower-emphasis, borderless
 * action for new call sites.
 */
type ActionButtonProps = Omit<ButtonBaseProps, "variant"> & { children?: ReactNode };

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
    <Badge
      tone="brand"
      size="xs"
      title="Demo business: figures and AI activity are simulated, not real business activity or integrations."
      // The size token owns font-size; leading must follow the surrounding line box.
      style={{ lineHeight: "inherit" }}
      className="border-dashed"
    >
      Demo · simulated
    </Badge>
  );
}

export function LiveBadge() {
  return (
    <Badge
      tone="success"
      size="xs"
      title="Live business: data comes from the connected backend for your selected business."
      // The size token owns font-size; leading must follow the surrounding line box.
      style={{ lineHeight: "inherit" }}
    >
      Live · server
    </Badge>
  );
}

export type CardVariant = "default" | "interactive";

/**
 * `default` keeps the historical surface every page already relies on.
 * `interactive` adds the hover lift the linked project/agent/marketplace grids
 * applied by hand; the wrapping `<Link>` still owns focus and navigation.
 * Variant classes come before `className`, so caller overrides keep winning.
 */
const cardVariants: Record<CardVariant, string> = {
  default: "",
  interactive: "transition-transform duration-200 hover:-translate-y-0.5",
};

export function Card({
  variant = "default",
  className,
  children,
}: {
  variant?: CardVariant;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-card border border-line bg-surface shadow-card",
        cardVariants[variant],
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

export function PrimaryButton(props: ActionButtonProps) {
  return <Button variant="primary" {...props} />;
}

export function GhostButton(props: ActionButtonProps) {
  return <Button variant="secondary" {...props} />;
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
    <Link href={href} title={title} className={cn(buttonClass({ variant: "primary" }), className)}>
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
    <Link href={href} className={cn(buttonClass({ variant: "secondary" }), className)}>
      {children}
    </Link>
  );
}
