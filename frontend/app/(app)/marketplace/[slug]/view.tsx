"use client";

import { useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import { Check, FlaskConical, Star } from "lucide-react";
import { Card, GhostButton, GhostLink, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";
import { HireButton, PackInstallButton } from "@/components/system/HireButton";
import { hireListing, installListing } from "@/lib/hiring";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveMarketplaceOne, useLivePackOne } from "@/lib/store/live";
import { useMarketplace } from "@/lib/store/app";
import type { Pack } from "@/lib/domain/types";

export function MarketplaceDetail() {
  const params = useParams<{ slug: string }>();
  const rawSlug = Array.isArray(params.slug) ? params.slug[0] : params.slug;
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveMarketplaceDetail businessId={currentBusinessId} slug={rawSlug} />;
  }
  return <DemoMarketplaceDetail slug={rawSlug} />;
}

function DemoMarketplaceDetail({ slug }: { slug: string }) {
  const listings = useMarketplace((s) => s.listings);
  const hiredSlugs = useMarketplace((s) => s.hiredSlugs);
  const installedSlugs = useMarketplace((s) => s.installedSlugs);

  const item = useMemo(() => listings.find((l) => l.slug === slug), [listings, slug]);

  // No silent fallback to listings[0]: an unknown slug must not show a
  // different product under the requested URL.
  if (!item) {
    return (
      <>
        <PageHeader title="Marketplace" sub="Demo catalog" />
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Listing not found</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            No demo listing has this slug. Nothing is shown in its place.
          </p>
          <div className="mt-3 flex justify-center">
            <GhostLink href="/marketplace">Back to marketplace</GhostLink>
          </div>
        </Card>
      </>
    );
  }

  const isPack = item.category === "pack";
  const done = isPack ? installedSlugs.includes(item.slug) : hiredSlugs.includes(item.slug);

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
            <h3 className="flex items-center gap-2 text-[13px] font-bold"><FlaskConical className="h-4 w-4" /> Sandbox — not available</h3>
            <p className="mt-1 text-[12.5px] leading-5 text-ink-500">
              Sandbox testing is not implemented in this build. Nothing is run here, and no test
              result is simulated.
            </p>
            <div className="mt-2">
              <GhostButton disabled title="Sandbox testing is not available in this build">
                Run test — unavailable
              </GhostButton>
            </div>
          </Card>
        </div>
        <Card className="h-fit p-4">
          <p className="flex items-center gap-1 text-[13px]"><Star className="h-4 w-4 fill-amber-400 text-amber-400" /> <b>{item.rating.toFixed(1)}/5</b> · {item.installs} installs</p>
          <p className="mt-2 text-[22px] font-bold tabular-nums">{item.price}</p>
          <p className="text-[12px] text-ink-400">One bill through MUSE SPARK. Cancel anytime.</p>
          <div className="mt-3 flex flex-col gap-2">
            {done ? (
              <p className="flex items-center gap-1.5 text-[13px] font-bold text-emerald-600">
                <Check className="h-4 w-4" /> {isPack ? "Installed" : "On your team"}
              </p>
            ) : (
              <PrimaryButton onClick={() => (isPack ? installListing(item.slug) : hireListing(item.slug))}>
                {isPack ? "Install Pack" : "Hire"} — {item.price}
              </PrimaryButton>
            )}
            {isPack && (
              <p className="text-[12px] leading-5 text-ink-500 dark:text-slate-400">
                Installs the bundled AI team and an onboarding workflow. Nothing runs until you start
                it.
              </p>
            )}
            <GhostButton disabled title="Creator messaging is not available in this build">
              Talk to creator — unavailable
            </GhostButton>
          </div>
        </Card>
      </div>
    </>
  );
}

function LiveMarketplaceDetail({ businessId, slug }: { businessId: string; slug: string }) {
  const { status, listing, notFound, error, load, reset } = useLiveMarketplaceOne();

  useEffect(() => {
    void load(businessId, slug);
    return () => reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, slug]);

  if (status === "loading" || status === "idle") {
    return (
      <>
        <PageHeader title="Marketplace" sub="Loading listing…" badge={<LiveBadge />} />
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading listing…</Card>
      </>
    );
  }

  if (status === "error" || !listing) {
    return (
      <>
        <PageHeader title="Marketplace" sub="Server catalog" badge={<LiveBadge />} />
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">
            {notFound ? "Listing not found" : "Couldn't load listing"}
          </p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            {notFound ? "This listing doesn't exist in the catalog." : error}
          </p>
          {!notFound && (
            <div className="mt-3 flex justify-center">
              <PrimaryButton className="justify-center" onClick={() => void load(businessId, slug)}>
                Retry
              </PrimaryButton>
            </div>
          )}
        </Card>
      </>
    );
  }

  if (listing.category === "pack") {
    return <LivePackDetail businessId={businessId} slug={slug} />;
  }

  const item = listing;
  return (
    <>
      <p className="text-[12px] text-ink-400">Marketplace › {item.name}</p>
      <PageHeader
        title={item.name}
        sub={`${item.role} · ★${item.rating.toFixed(1)} · ${item.installs} installs · ${item.price} ${item.priceNote}`}
        badge={<LiveBadge />}
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
        </div>
        <Card className="h-fit p-4">
          <p className="flex items-center gap-1 text-[13px]"><Star className="h-4 w-4 fill-amber-400 text-amber-400" /> <b>{item.rating.toFixed(1)}/5</b> · {item.installs} installs</p>
          <p className="mt-2 text-[22px] font-bold tabular-nums">{item.price}</p>
          <p className="text-[12px] text-ink-400">One bill through MUSE SPARK. Cancel anytime.</p>
          <div className="mt-3 flex flex-col gap-2">
            {item.category === "employee" ? (
              <HireButton businessId={businessId} slug={item.slug} hired={item.hired === true} price={item.price} />
            ) : (
              <p
                title="No supported hire or install action exists for this catalog category in this build."
                className="rounded-[10px] border border-dashed border-mist-200 px-3 py-2 text-[12.5px] text-ink-400 dark:border-white/10"
              >
                Not available for this listing type.
              </p>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}

function PackAgentsCard({ pack }: { pack: Pack }) {
  return (
    <Card className="p-4">
      <h3 className="text-[13px] font-bold">Included agents ({pack.agents.length})</h3>
      <div className="mt-2 flex flex-col gap-2">
        {pack.agents.map((a) => (
          <div key={a.slug} className="flex items-center justify-between gap-2 rounded-lg bg-mist-50 p-2 dark:bg-white/5">
            <div>
              <p className="text-[13px] font-semibold">{a.name}</p>
              <p className="text-[12px] text-ink-400">{a.role}</p>
            </div>
            <span
              className={
                a.action === "blocked"
                  ? "rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-bold text-red-700 dark:bg-red-500/15 dark:text-red-300"
                  : a.action === "reuse"
                    ? "rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-bold text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"
                    : "rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-bold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
              }
            >
              {a.action === "reuse" ? "Existing" : a.action === "blocked" ? "Disabled" : "Install"}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function PackWorkflowCard({ pack }: { pack: Pack }) {
  if (!pack.workflow) return null;
  return (
    <Card className="p-4">
      <h3 className="text-[13px] font-bold">Workflow: {pack.workflow.title}</h3>
      <p className="mt-1 text-[12.5px] text-ink-500">{pack.workflow.goal}</p>
      <ol className="mt-2 flex flex-col gap-1.5 text-[12.5px]">
        {pack.workflow.tasks.map((t, i) => (
          <li key={t.key} className="rounded-lg bg-mist-50 p-2 dark:bg-white/5">
            <b>
              {i + 1}. {t.title}
            </b>{" "}
            <span className="text-ink-400">· {t.ownerAgentSlug}</span>
            {t.dependsOn.length > 0 && (
              <span className="text-ink-400"> · after {t.dependsOn.join(", ")}</span>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

function LivePackDetail({ businessId, slug }: { businessId: string; slug: string }) {
  const { status, preview, notFound, error, load, reset } = useLivePackOne();

  useEffect(() => {
    void load(businessId, slug);
    return () => reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, slug]);

  if (status === "loading" || status === "idle") {
    return (
      <>
        <PageHeader title="Marketplace" sub="Loading pack…" badge={<LiveBadge />} />
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading pack…</Card>
      </>
    );
  }

  if (status === "error" || !preview) {
    return (
      <>
        <PageHeader title="Marketplace" sub="Server catalog" badge={<LiveBadge />} />
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">
            {notFound ? "Pack not found" : "Couldn't load pack"}
          </p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">
            {notFound ? "This pack doesn't exist in the catalog." : error}
          </p>
          {!notFound && (
            <div className="mt-3 flex justify-center">
              <PrimaryButton className="justify-center" onClick={() => void load(businessId, slug)}>
                Retry
              </PrimaryButton>
            </div>
          )}
        </Card>
      </>
    );
  }

  const pack = preview.pack;
  return (
    <>
      <p className="text-[12px] text-ink-400">Marketplace › {pack.name}</p>
      <PageHeader
        title={pack.name}
        sub={`Pack v${pack.version} · ★${pack.rating.toFixed(1)} · ${pack.price} ${pack.priceNote}`}
        badge={<LiveBadge />}
      />
      <div className="grid gap-3 xl:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-3">
          <Card className="p-4">
            <h3 className="text-[13px] font-bold">What this pack installs</h3>
            <p className="mt-1 text-[12.5px] leading-5 text-ink-500">{pack.description}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {pack.agents.flatMap((a) => a.capabilities).map((c) => (
                <span key={c} className="rounded-full bg-mist-100 px-2.5 py-1 text-[12.5px] font-medium dark:bg-white/10">
                  {c}
                </span>
              ))}
            </div>
          </Card>
          <PackAgentsCard pack={pack} />
          <PackWorkflowCard pack={pack} />
        </div>
        <Card className="h-fit p-4">
          <p className="flex items-center gap-1 text-[13px]">
            <Star className="h-4 w-4 fill-amber-400 text-amber-400" /> <b>{pack.rating.toFixed(1)}/5</b> ·{" "}
            {pack.installs} installs
          </p>
          <p className="mt-2 text-[22px] font-bold tabular-nums">{pack.price}</p>
          <p className="text-[12px] text-ink-400">One bill through MUSE SPARK. Cancel anytime.</p>
          <div className="mt-3 flex flex-col gap-2">
            {pack.installed ? (
              <p className="flex items-center gap-1.5 text-[13px] font-bold text-emerald-600">
                <Check className="h-4 w-4" /> Installed v{pack.installedVersion}
              </p>
            ) : preview.canInstall ? (
              <PackInstallButton businessId={businessId} slug={pack.slug} installed={false} price={pack.price} />
            ) : (
              <div className="flex flex-col gap-1.5">
                {preview.blockers.map((b) => (
                  <p key={b.code} role="alert" className="text-[12px] leading-5 text-red-600 dark:text-red-300">
                    {b.message}
                  </p>
                ))}
              </div>
            )}
            <p className="text-[12px] leading-5 text-ink-500 dark:text-slate-400">
              Nothing runs on install — the workflow starts only when you start it.
            </p>
          </div>
        </Card>
      </div>
    </>
  );
}
