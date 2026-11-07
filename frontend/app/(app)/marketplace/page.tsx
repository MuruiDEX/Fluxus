"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, Search, Star } from "lucide-react";
import { Avatar } from "@/components/system/badges";
import {
  Card,
  LiveBadge,
  PageHeader,
  PrimaryButton,
  SkeletonCard,
} from "@/components/system/ui";
import { StaggerGroup, StaggerItem } from "@/components/system/motion";
import { HireButton, PackInstallButton } from "@/components/system/HireButton";
import { hireListing, installListing } from "@/lib/hiring";
import { MARKETPLACE_TABS, selectListings, type MarketplaceTab } from "@/lib/marketplace-tabs";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveMarketplace } from "@/lib/store/live";
import { useMarketplace } from "@/lib/store/app";
import { cn } from "@/lib/cn";

const tabs = MARKETPLACE_TABS;

export default function MarketplacePage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveMarketplaceView businessId={currentBusinessId} />;
  }
  return <DemoMarketplaceView />;
}

function DemoMarketplaceView() {
  const listings = useMarketplace((s) => s.listings);
  const hiredSlugs = useMarketplace((s) => s.hiredSlugs);
  const installedSlugs = useMarketplace((s) => s.installedSlugs);
  const [tab, setTab] = useState<MarketplaceTab>("Recommended");
  const [q, setQ] = useState("");

  const visible = selectListings(listings, tab, q);

  return (
    <>
      <PageHeader title="Marketplace" sub="Hire AI employees and install capabilities. One bill, no API keys." />
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-9 max-w-sm flex-1 items-center gap-2 rounded-[10px] border border-mist-200 bg-white px-3 dark:border-white/10 dark:bg-night-900">
          <Search className="h-4 w-4 text-ink-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search AI employees, automations, templates…" className="w-full bg-transparent text-[13px] outline-none" />
        </div>
        <span className="ml-auto flex items-center gap-1.5 text-[12.5px] text-ink-500">
          <Avatar name="Alex Morgan" hue={217} size={20} /> Your team ({hiredSlugs.length})
        </span>
      </div>
      <div role="group" aria-label="Marketplace filters" className="flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            aria-pressed={tab === t}
            onClick={() => setTab(t)}
            className={cn(
              "rounded-full px-3 py-1.5 text-[12.5px] font-medium transition-colors duration-150",
              tab === t ? "bg-brand-500 text-white" : "border border-mist-200 bg-white text-ink-500 dark:border-white/10 dark:bg-night-900",
            )}
          >
            {t}
          </button>
        ))}
      </div>
      <h2 className="text-[14px] font-bold">
        {tab === "Recommended" ? "Recommended for your business" : `${tab} listings`}
      </h2>
      {visible.length === 0 ? (
        <Card className="p-8 text-center text-[13px] text-ink-400">
          {listings.length === 0
            ? "The catalog is empty."
            : `No listings match ${q.trim() ? "this search" : `the “${tab}” filter`}.`}
        </Card>
      ) : (
        <StaggerGroup className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {visible.map((l) => {
            const isPack = l.category === "pack";
            const done = isPack ? installedSlugs.includes(l.slug) : hiredSlugs.includes(l.slug);
            return (
              <StaggerItem key={l.slug} className="h-full">
                <Card className="flex h-full flex-col gap-2 p-4 transition-transform duration-200 hover:-translate-y-0.5">
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
                      {done ? (
                        <span className="flex items-center gap-1 text-[12.5px] font-bold text-emerald-600">
                          <Check className="h-4 w-4" /> {isPack ? "Installed" : "Hired"}
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => (isPack ? installListing(l.slug) : hireListing(l.slug))}
                          className="h-8 rounded-lg bg-brand-500 px-3.5 text-[12.5px] font-semibold text-white transition-opacity hover:opacity-90"
                        >
                          {isPack ? "Install Pack" : "Hire"}
                        </button>
                      )}
                    </div>
                  </div>
                </Card>
              </StaggerItem>
            );
          })}
        </StaggerGroup>
      )}
    </>
  );
}

function LiveMarketplaceView({ businessId }: { businessId: string }) {
  const { status, listings, total, error, load, reset } = useLiveMarketplace();
  const [q, setQ] = useState("");

  useEffect(() => {
    void load(businessId);
    return () => reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId]);

  const visible = listings.filter((l) =>
    (l.name + l.role + l.blurb).toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <>
      <PageHeader
        title="Marketplace"
        sub="Hire AI employees from the server catalog. One bill, no API keys."
        badge={<LiveBadge />}
      />
      <div className="flex h-9 max-w-sm items-center gap-2 rounded-[10px] border border-mist-200 bg-white px-3 dark:border-white/10 dark:bg-night-900">
        <Search className="h-4 w-4 text-ink-400" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search AI employees…" className="w-full bg-transparent text-[13px] outline-none" />
      </div>
      {status === "loading" || status === "idle" ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" role="status" aria-label="Loading catalog">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : status === "error" ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Couldn&apos;t load the catalog</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{error}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton className="justify-center" onClick={() => void load(businessId)}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      ) : visible.length === 0 ? (
        <Card className="p-8 text-center text-[13px] text-ink-400">
          {listings.length === 0
            ? "The catalog is empty — new listings appear here when published."
            : "No listings match this search."}
        </Card>
      ) : (
        <>
          <h2 className="text-[14px] font-bold">Available now ({total})</h2>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {visible.map((l) => (
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
                  <div className="flex items-center gap-2">
                    <Link href={`/marketplace/${l.slug}`} className="text-[12.5px] font-semibold text-brand-500 hover:underline">
                      View
                    </Link>
                    {l.category === "employee" ? (
                      <HireButton businessId={businessId} slug={l.slug} hired={l.hired === true} price={l.price} />
                    ) : l.category === "pack" ? (
                      <PackInstallButton
                        businessId={businessId}
                        slug={l.slug}
                        installed={l.installed === true}
                        price={l.price}
                      />
                    ) : (
                      <span
                        title="No supported hire or install action exists for this catalog category in this build."
                        className="rounded-lg border border-dashed border-mist-200 px-2.5 py-1 text-[12px] font-medium text-ink-400 dark:border-white/10"
                      >
                        Not available
                      </span>
                    )}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </>
  );
}
