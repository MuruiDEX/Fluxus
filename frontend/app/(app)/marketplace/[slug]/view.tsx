"use client";

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { Check, FlaskConical, Star } from "lucide-react";
import { Card, GhostButton, PageHeader, PrimaryButton } from "@/components/system/ui";
import { hireListing } from "@/lib/hiring";
import { useMarketplace } from "@/lib/store/app";

export function MarketplaceDetail() {
  const params = useParams<{ slug: string }>();
  const slug = Array.isArray(params.slug) ? params.slug[0] : params.slug;
  const listings = useMarketplace((s) => s.listings);
  const hiredSlugs = useMarketplace((s) => s.hiredSlugs);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const item = useMemo(() => listings.find((l) => l.slug === slug) ?? listings[0], [listings, slug]);
  const hired = hiredSlugs.includes(item.slug);

  const runTest = () => {
    setTesting(true);
    setTestResult(null);
    setTimeout(() => {
      setTesting(false);
      setTestResult("Qualified 8/10 sample leads · est. $0.04 · 42s · quality 4.7/5");
    }, 1400);
  };

  return (
    <>
      <p className="text-[12px] text-ink-400">Marketplace › {item.name}</p>
      <PageHeader
        title={item.name}
        sub={`${item.role} · ★${item.rating.toFixed(1)} · ${item.installs} installs · ${item.price} ${item.priceNote}`}
      />
      <div className="grid gap-3 xl:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-3">
          <Card className="p-4">
            <h3 className="text-[13px] font-bold">Capabilities</h3>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {item.capabilities.map((c) => (
                <span key={c} className="rounded-full bg-mist-100 px-2.5 py-1 text-[12.5px] font-medium dark:bg-white/10">{c}</span>
              ))}
            </div>
            <h3 className="mb-1.5 mt-4 text-[13px] font-bold">Permissions</h3>
            <div className="flex flex-col gap-1.5 text-[12.5px]">
              <p className="rounded-lg bg-emerald-50 p-2 dark:bg-emerald-500/10"><b>READ:</b> {item.permissions.read.join(", ")}</p>
              <p className="rounded-lg bg-blue-50 p-2 dark:bg-blue-500/10"><b>WRITE:</b> {item.permissions.write.join(", ") || "—"}</p>
              <p className="rounded-lg bg-red-50 p-2 dark:bg-red-500/10"><b>NO:</b> {item.permissions.denied.join(", ")}</p>
            </div>
          </Card>
          <Card className="p-4">
            <h3 className="flex items-center gap-2 text-[13px] font-bold"><FlaskConical className="h-4 w-4" /> Sandbox — try before hiring</h3>
            <p className="mt-1 text-[12.5px] text-ink-500">Give it a realistic task, e.g. “Qualify these 10 sample leads.”</p>
            <div className="mt-2 flex gap-2">
              <GhostButton onClick={runTest}>{testing ? "Running…" : "Run test"}</GhostButton>
              {testResult && <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-emerald-600"><Check className="h-4 w-4" /> {testResult}</p>}
            </div>
          </Card>
        </div>
        <Card className="h-fit p-4">
          <p className="flex items-center gap-1 text-[13px]"><Star className="h-4 w-4 fill-amber-400 text-amber-400" /> <b>{item.rating.toFixed(1)}/5</b> · {item.installs} installs</p>
          <p className="mt-2 text-[22px] font-bold tabular-nums">{item.price}</p>
          <p className="text-[12px] text-ink-400">One bill through MUSE SPARK. Cancel anytime.</p>
          <div className="mt-3 flex flex-col gap-2">
            {hired ? (
              <p className="flex items-center gap-1.5 text-[13px] font-bold text-emerald-600"><Check className="h-4 w-4" /> On your team</p>
            ) : (
              <PrimaryButton onClick={() => hireListing(item.slug)}>
                {item.category === "pack" ? "Install Pack" : "Hire"} — {item.price}
              </PrimaryButton>
            )}
            <GhostButton>Talk to creator</GhostButton>
          </div>
        </Card>
      </div>
    </>
  );
}
