"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, CheckCircle2, CircleAlert, Lightbulb, TrendingUp } from "lucide-react";
import { AgentStatusBadge, Avatar, ProgressBar } from "@/components/system/badges";
import { Card, DemoBadge, MetricCard, PageHeader, PrimaryButton } from "@/components/system/ui";
import { useActivity, useAgents, useApprovals, useBusiness, useProjects } from "@/lib/store/app";

const briefing = [
  { icon: CircleAlert, tone: "text-red-500", text: "Competitor X reduced pricing by 10%.", action: "Investigate", goal: "Respond to competitor X cutting prices by 10%." },
  { icon: TrendingUp, tone: "text-amber-500", text: "Search traffic for “personal trainer Kraków” increased 23%.", action: "Create Project", goal: "Capture rising search demand for personal trainer Krakow." },
  { icon: Lightbulb, tone: "text-sky-500", text: "Customer response time increased from 8m to 21m.", action: "Investigate", goal: "Fix customer response time rising from 8 to 21 minutes." },
  { icon: CheckCircle2, tone: "text-emerald-500", text: "Launch a retention campaign for inactive customers.", action: "Create Project", goal: "Launch a retention campaign for inactive customers." },
];

export default function HomePage() {
  const { business } = useBusiness();
  const { projects } = useProjects();
  const agents = useAgents((s) => s.agents);
  const approvals = useApprovals((s) => s.approvals);
  const events = useActivity((s) => s.events);
  const m = business.metrics;
  const pending = approvals.filter((a) => a.status === "pending");
  const router = useRouter();

  return (
    <>
      <PageHeader
        title={`Good morning, ${business.ownerName}`}
        badge={<DemoBadge />}
        sub={`Your AI company is working on ${projects.length} project${projects.length === 1 ? "" : "s"}. Here's what's happening.`}
        actions={
          <Link href="/projects/new">
            <PrimaryButton>+ New Project</PrimaryButton>
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <MetricCard label="Revenue" value={`$${business.metrics.revenue.toLocaleString("en-US")}`} delta={m.revenueDelta} spark={business.revenueSeries.map((r) => r.value)} />
        <MetricCard label="New Customers" value={String(m.customers)} delta={m.customersDelta} spark={[42, 55, 51, 66, 74, 88]} />
        <MetricCard label="Conversion" value={`${m.conversion}%`} delta={m.conversionDelta} invert spark={[62, 58, 60, 52, 55, 51]} />
        <Card className="flex items-center gap-3 p-4">
          <div className="relative h-14 w-14 shrink-0">
            <svg viewBox="0 0 36 36" className="h-14 w-14 -rotate-90">
              <circle cx="18" cy="18" r="15.5" fill="none" strokeWidth="4" className="stroke-mist-200 dark:stroke-white/10" />
              <circle
                cx="18"
                cy="18"
                r="15.5"
                fill="none"
                strokeWidth="4"
                strokeLinecap="round"
                stroke="url(#health)"
                strokeDasharray={`${business.healthScore}, 100`}
              />
              <defs>
                <linearGradient id="health" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#2563eb" />
                  <stop offset="100%" stopColor="#06b6d4" />
                </linearGradient>
              </defs>
            </svg>
            <span className="absolute inset-0 flex items-center justify-center text-[13px] font-bold tabular-nums">
              {business.healthScore}
            </span>
          </div>
          <div>
            <p className="text-[12px] font-medium text-ink-500 dark:text-slate-400">Business Health</p>
            <p className="text-[15px] font-bold">{business.healthScore}/100</p>
          </div>
        </Card>
      </div>

      <div className="grid gap-3 xl:grid-cols-3">
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-[15px] font-bold">CEO Briefing</h2>
            <span className="text-[11px] text-ink-400">Today</span>
          </div>
          <div className="flex flex-col">
            {briefing.map((b, i) => (
              <div key={i} className="flex items-start gap-2.5 border-b border-mist-100 py-2.5 last:border-0 dark:border-white/5">
                <b.icon className={`mt-0.5 h-4 w-4 shrink-0 ${b.tone}`} />
                <p className="flex-1 text-[13px] leading-5">{b.text}</p>
                <button
                  onClick={() => router.push(`/projects/new?goal=${encodeURIComponent(b.goal)}`)}
                  className="shrink-0 text-[12px] font-semibold text-brand-500 hover:underline"
                >
                  {b.action}
                </button>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-[15px] font-bold">Active Projects</h2>
            <Link href="/projects" className="flex items-center gap-1 text-[12px] font-semibold text-brand-500 hover:underline">
              View all <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          <div className="flex flex-col gap-3">
            {projects.map((p) => (
              <Link key={p.id} href={`/projects/${p.id}`} className="group">
                <div className="mb-1 flex items-center justify-between text-[13px]">
                  <span className="font-medium group-hover:text-brand-500">{p.title}</span>
                  <span className="tabular-nums text-ink-500">{p.progress}%</span>
                </div>
                <ProgressBar value={p.progress} />
              </Link>
            ))}
            <div className="rounded-xl bg-mist-50 p-3 text-[12.5px] dark:bg-white/5">
              <p className="font-semibold">Marketing Campaign <span className="float-right tabular-nums text-ink-500">42%</span></p>
              <ProgressBar value={42} className="mt-1.5" />
            </div>
            <div className="rounded-xl bg-mist-50 p-3 text-[12.5px] dark:bg-white/5">
              <p className="font-semibold">Website Development <span className="float-right tabular-nums text-ink-500">76%</span></p>
              <ProgressBar value={76} className="mt-1.5" />
            </div>
          </div>
        </Card>

        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-[15px] font-bold">AI Activity</h2>
            <Link href="/office" className="flex items-center gap-1 text-[12px] font-semibold text-brand-500 hover:underline">
              Open office <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {agents.slice(0, 5).map((a) => (
              <span key={a.id} className="flex items-center gap-1.5 rounded-full border border-mist-200 py-0.5 pl-0.5 pr-2 dark:border-white/10">
                <Avatar name={a.name} hue={a.avatarHue} size={20} />
                <span className="text-[11px] font-medium">{a.role}</span>
              </span>
            ))}
          </div>
          <div className="flex flex-col">
            {events.slice(0, 4).map((e) => (
              <div key={e.id} className="flex items-start gap-2 border-b border-mist-100 py-2 text-[12.5px] last:border-0 dark:border-white/5">
                <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-sky-400" />
                <p className="flex-1 leading-5">
                  <span className="font-semibold">{e.agentName}</span> — {e.text}
                </p>
                <span className="shrink-0 text-[11px] text-ink-400">{e.at}</span>
              </div>
            ))}
          </div>
          {pending.length > 0 && (
            <Link
              href="/approvals"
              className="mt-3 flex items-center justify-between rounded-xl bg-orange-50 px-3 py-2.5 text-[13px] font-medium text-orange-700 dark:bg-orange-500/10 dark:text-orange-300"
            >
              {pending.length} approval{pending.length === 1 ? "" : "s"} waiting for you
              <ArrowRight className="h-4 w-4" />
            </Link>
          )}
        </Card>
      </div>

      <Card className="p-4">
        <h2 className="mb-2 text-[15px] font-bold">Live now</h2>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {agents.filter((a) => a.status === "working" || a.status === "waiting_dependency").slice(0, 4).map((a) => (
            <div key={a.id} className="flex items-center gap-2.5 rounded-xl border border-mist-200 p-2.5 dark:border-white/10">
              <Avatar name={a.name} hue={a.avatarHue} size={30} />
              <div className="min-w-0">
                <p className="truncate text-[12.5px] font-semibold">{a.role}</p>
                <p className="truncate text-[11.5px] text-ink-500">{a.currentTask}</p>
              </div>
              <div className="ml-auto"><AgentStatusBadge status={a.status} live /></div>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
