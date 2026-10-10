import { Suspense } from "react";
import { marketplace } from "@/lib/data/seed-northstar";
import { MarketplaceDetail } from "./view";

export function generateStaticParams() {
  return marketplace.map((l) => ({ slug: l.slug }));
}

export default function MarketplaceDetailPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-400">Loading listing…</p>}>
      <MarketplaceDetail />
    </Suspense>
  );
}
