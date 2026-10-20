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
        <PrimaryButton className="mt-2">
          {cta} <ArrowRight className="h-4 w-4" />
        </PrimaryButton>
      </Card>
    </>
  );
}
