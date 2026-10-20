"use client";

import { UsagePanel } from "@/components/system/UsagePanel";
import { Card, LiveBadge, PageHeader } from "@/components/system/ui";
import { useSession } from "@/lib/store/auth";
import { isLiveContext } from "@/lib/store/live";

function DemoUsageView() {
  return (
    <>
      <PageHeader
        title="Usage"
        sub="AI usage accounting is available for connected businesses."
      />
      <Card className="p-4 text-[13px] text-ink-500 dark:text-slate-400">
        Connect a business in LIVE mode to see every billable AI call — task
        executions plus Team Lead calls (questions, summaries, planning and
        finalization), including calls with no project yet.
      </Card>
    </>
  );
}

function LiveUsageView({ businessId }: { businessId: string }) {
  return (
    <>
      <PageHeader
        title="Usage"
        sub="Every billable AI call across the business, newest first. Confirmed spend is kept separate from estimated and unpriced calls."
        badge={<LiveBadge />}
      />
      <UsagePanel businessId={businessId} />
    </>
  );
}

export default function UsagePage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveUsageView businessId={currentBusinessId} />;
  }
  return <DemoUsageView />;
}
