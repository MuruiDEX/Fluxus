import { ArrowRight, Orbit } from "lucide-react";
import { Badge } from "@/components/system/badges";
import { Card, PageHeader, PrimaryButton } from "@/components/system/ui";

/**
 * Shared pattern for preview modules that are intentionally not operational
 * yet. The UI is polished, but the badge and disabled action state are
 * explicit: nothing here is connected to a backend or a fake integration.
 */
export function DeferredPage({
  title,
  body,
  cta,
}: {
  title: string;
  body: string;
  cta: string;
}) {
  return (
    <>
      <PageHeader
        title={title}
        badge={<Badge tone="warning">Planned module</Badge>}
        sub="Preview surface · not connected to a backend yet."
      />
      <Card className="relative max-w-2xl overflow-hidden p-6">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-accent/10 blur-3xl"
        />
        <span
          aria-hidden="true"
          className="absolute right-6 top-6 hidden h-16 w-16 items-center justify-center rounded-full border border-line bg-surface-raised text-brand sm:flex"
        >
          <Orbit className="h-7 w-7" />
        </span>
        <div className="relative">
          <p className="text-micro font-semibold uppercase tracking-[0.16em] text-fg-subtle">
            On the roadmap
          </p>
          <p className="mt-2 text-heading-sm font-bold text-fg">{title} is coming next</p>
          <p className="mt-2 max-w-lg text-body-sm leading-6 text-fg-muted">{body}</p>
          {/* Inert on purpose: the capability is not built yet, so the control
              says so instead of looking like a click that does nothing. */}
          <PrimaryButton className="mt-4" disabled title="Not available yet">
            {cta} <ArrowRight className="h-4 w-4" />
          </PrimaryButton>
          <p className="mt-2 text-caption text-fg-subtle">
            Not available yet — no request is sent.
          </p>
        </div>
      </Card>
    </>
  );
}
