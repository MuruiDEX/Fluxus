"use client";

import { Suspense } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Bot,
  Brain,
  Briefcase,
  CheckSquare,
  Cpu,
  FileText,
  FlaskConical,
  Globe,
  Home,
  KanbanSquare,
  Layers,
  Megaphone,
  Palette,
  Plug,
  ReceiptText,
  Settings,
  ShoppingBag,
  Sparkles,
  Users,
  Wallet,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { useAgents, useApprovals, useBusiness } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgentsList, useLiveApprovals } from "@/lib/store/live";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Home;
  badgeKind?: "office" | "approvals";
}

const groups: { label?: string; items: NavItem[] }[] = [
  {
    items: [
      { href: "/", label: "Home", icon: Home },
      { href: "/office", label: "AI Office", icon: Sparkles, badgeKind: "office" },
      { href: "/projects", label: "Projects", icon: Briefcase },
      { href: "/tasks", label: "Tasks", icon: CheckSquare },
      { href: "/agents", label: "Agents", icon: Bot },
      { href: "/marketplace", label: "Marketplace", icon: ShoppingBag },
    ],
  },
  {
    label: "Operate",
    items: [
      { href: "/automations", label: "Automations", icon: Zap },
      { href: "/crm", label: "CRM", icon: KanbanSquare },
      { href: "/customers", label: "Customers", icon: Users },
      { href: "/marketing", label: "Marketing", icon: Megaphone },
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/finance", label: "Finance", icon: Wallet },
    ],
  },
  {
    label: "Create",
    items: [
      { href: "/research", label: "Research", icon: FlaskConical },
      { href: "/brand", label: "Brand Studio", icon: Palette },
      { href: "/website", label: "Website Studio", icon: Globe },
      { href: "/documents", label: "Documents", icon: FileText },
      { href: "/brain", label: "Business Brain", icon: Brain },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/approvals", label: "Approvals", icon: Layers, badgeKind: "approvals" },
      { href: "/usage", label: "Usage", icon: ReceiptText },
      { href: "/integrations", label: "Integrations", icon: Plug },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

function OfficeBadge() {
  const { status, currentBusinessId } = useSession();
  const demoWorking = useAgents(
    (s) =>
      s.agents.filter(
        (agent) => agent.hired && (agent.status === "working" || agent.status === "thinking"),
      ).length,
  );
  const liveWorking = useLiveAgentsList(
    (s) =>
      s.agents.filter(
        (agent) => agent.hired && (agent.status === "working" || agent.status === "thinking"),
      ).length,
  );
  const working = isLiveContext(status, currentBusinessId) ? liveWorking : demoWorking;
  if (working === 0) return null;
  return <BadgePill label={`${working} working`} />;
}

function ApprovalsBadge() {
  const { status, currentBusinessId } = useSession();
  const demoPending = useApprovals(
    (s) => s.approvals.filter((approval) => approval.status === "pending").length,
  );
  const livePending = useLiveApprovals(
    (s) => s.approvals.filter((approval) => approval.status === "pending").length,
  );
  const pending = isLiveContext(status, currentBusinessId) ? livePending : demoPending;
  if (pending === 0) return null;
  return <BadgePill label={String(pending)} />;
}

function BadgePill({ label }: { label: string }) {
  return (
    <span className="rounded-full bg-mist-100 px-1.5 py-px text-[10px] font-semibold text-ink-500 dark:bg-white/10 dark:text-slate-300">
      {label}
    </span>
  );
}

export function SidebarLinks() {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-4">
      {groups.map((g, gi) => (
        <div key={gi} className="flex flex-col gap-0.5">
          {g.label && (
            <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
              {g.label}
            </p>
          )}
          {g.items.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            // Badge hooks must run unconditionally — call inside a child component.
            return <NavLink key={item.href} item={item} active={active} />;
          })}
        </div>
      ))}
    </nav>
  );
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      href={item.href}
      className={cn(
        "group flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] font-medium transition-colors",
        active
          ? "bg-brand-50 text-brand-600 dark:bg-brand-500/15 dark:text-sky-300"
          : "text-ink-700 hover:bg-mist-100 dark:text-slate-300 dark:hover:bg-white/5",
      )}
    >
      <item.icon className="h-4 w-4 shrink-0 opacity-80" />
      <span className="flex-1 truncate">{item.label}</span>
      {item.badgeKind === "office" && <OfficeBadge />}
      {item.badgeKind === "approvals" && <ApprovalsBadge />}
    </Link>
  );
}

function WorkspaceFooter() {
  const { status, currentBusinessId, businessNames } = useSession();
  const { business } = useBusiness();
  if (status === "authenticated" && currentBusinessId) {
    const name = businessNames[currentBusinessId] ?? `${currentBusinessId.slice(0, 8)}…`;
    return (
      <div className="mt-auto rounded-xl border border-emerald-200 bg-emerald-50/60 p-2.5 dark:border-emerald-500/30 dark:bg-emerald-500/10">
        <p className="truncate text-[12px] font-bold">{name}</p>
        <p className="mt-0.5 text-[11px] leading-4 text-emerald-700 dark:text-emerald-300">
          Live business · server data only.
        </p>
      </div>
    );
  }
  return (
    <div className="mt-auto rounded-xl border border-dashed border-mist-200 p-2.5 dark:border-white/10">
      <p className="truncate text-[12px] font-bold">{business.name}</p>
      <p className="mt-0.5 text-[11px] leading-4 text-ink-400">
        Demo business · figures & AI activity are simulated.
      </p>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden w-[232px] shrink-0 flex-col gap-4 overflow-y-auto border-r border-mist-200 bg-white px-3 py-4 lg:flex dark:border-white/10 dark:bg-night-900">
      <Link href="/" className="flex items-center gap-2 px-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-cyan-500 text-white">
          <Cpu className="h-4 w-4" />
        </span>
        <span className="text-[13px] font-bold tracking-wide text-ink-950 dark:text-white">
          MUSE SPARK
        </span>
      </Link>
      <Suspense>
        <SidebarLinks />
      </Suspense>
      <WorkspaceFooter />
    </aside>
  );
}
