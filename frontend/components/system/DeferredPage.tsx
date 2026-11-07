import { ArrowRight } from "lucide-react";
import { Card, PageHeader, PrimaryButton } from "@/components/system/ui";

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
      <PageHeader title={title} sub="Deferred until the core AI-company loop is stable and verified." />
      <Card className="flex max-w-xl flex-col items-start gap-2 p-6">
        <p className="text-[15px] font-bold">{title} is coming next</p>
        <p className="text-[13px] leading-6 text-ink-500">{body}</p>
        {/* Inert on purpose: the capability is not built yet, so the control
            says so instead of looking like a click that does nothing. */}
        <PrimaryButton className="mt-2" disabled title="Not available yet">
          {cta} <ArrowRight className="h-4 w-4" />
        </PrimaryButton>
        <p className="text-[11.5px] text-ink-400">Not available yet — no request is sent.</p>
      </Card>
    </>
  );
}
