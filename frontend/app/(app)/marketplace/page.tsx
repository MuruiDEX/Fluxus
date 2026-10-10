"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, Search, Star } from "lucide-react";
import { Avatar } from "@/components/system/badges";
import { Card, PageHeader } from "@/components/system/ui";
import { hireListing } from "@/lib/hiring";
import { useMarketplace } from "@/lib/store/app";
import { cn } from "@/lib/cn";

const tabs = ["Recommended", "Trending", "Best Rated", "New", "Free", "Premium"] as const;

export default function MarketplacePage() {
  const listings = useMarketplace((s) => s.listings);
  const hiredSlugs = useMarketplace((s) => s.hiredSlugs);
  const [tab, setTab] = useState<(typeof tabs)[number]>("Recommended");
  const [q, setQ] = useState("");

  const visible = listings.filter((l) => (l.name + l.role + l.blurb).toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <PageHeader title="Marketplace" sub="Hire AI employees and install capabilities. One bill, no API keys." />
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-9 max-w-sm flex-1 items-center gap-2 rounded-[10px] border border-mist-200 bg-white px-3 dark:border-white/10 dark:bg-night-900">
          <Search className="h-4 w-4 text-ink-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search AI employees, automations, templates…" className="w-full bg-transparent text-[13px] outline-none" />
        </div>
        <span className="ml-auto flex items-center gap-1.5 text-[12.5px] text-ink-500">
          <Avatar name="Alex Morgan" hue={217} size={20} /> Your team (12)
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "rounded-full px-3 py-1.5 text-[12.5px] font-medium",
              tab === t ? "bg-brand-500 text-white" : "border border-mist-200 bg-white text-ink-500 dark:border-white/10 dark:bg-night-900",
            )}
          >
            {t}
          </button>
        ))}
      </div>
      <h2 className="text-[14px] font-bold">Recommended for your business</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {visible.map((l) => {
          const hired = hiredSlugs.includes(l.slug);
          return (
            <Card key={l.slug} className="flex flex-col gap-2 p-4">
              <div className="flex items-start justify-between">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-cyan-500 text-[15px] font-bold text-white">
                  {l.name[0]}
                </span>
                {l.badge && (
                  <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[10.5px] font-bold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
                    {l.badge}
                  </span>
                )}
              </div>
              <p className="text-[14.5px] font-bold">{l.name}</p>
              <p className="flex items-center gap-1 text-[12px] text-ink-400">
                <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" /> {l.rating.toFixed(1)} · {l.installs} installs
              </p>
              <p className="text-[13px] leading-5 text-ink-700 dark:text-slate-300">{l.blurb}</p>
              <div className="mt-auto flex items-center justify-between pt-2">
                <span className="text-[13.5px] font-bold tabular-nums">{l.price}</span>
                <div className="flex gap-2">
                  <Link href={`/marketplace/${l.slug}`} className="text-[12.5px] font-semibold text-brand-500 hover:underline">
                    View
                  </Link>
                  {hired ? (
                    <span className="flex items-center gap-1 text-[12.5px] font-bold text-emerald-600"><Check className="h-4 w-4" /> Hired</span>
                  ) : (
                    <button
                      onClick={() => hireListing(l.slug)}
                      className="h-8 rounded-lg bg-brand-500 px-3.5 text-[12.5px] font-semibold text-white hover:opacity-90"
                    >
                      {l.category === "pack" ? "Install Pack" : "Hire"}
                    </button>
                  )}
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
