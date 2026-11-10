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
import { Badge } from "./badges";
import { useAgents, useApprovals, useBusiness } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgentsList, useLiveApprovals } from "@/lib/store/live";
import { runtimeMode } from "@/lib/runtime-config";

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
  // In a LIVE deployment the seed counts must never stand in for an unloaded
  // or unreachable server: fall back to zero, not to demo data.
  const working =
    runtimeMode === "live" || isLiveContext(status, currentBusinessId) ? liveWorking : demoWorking;
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
  const pending =
    runtimeMode === "live" || isLiveContext(status, currentBusinessId) ? livePending : demoPending;
  if (pending === 0) return null;
  return <BadgePill label={String(pending)} />;
}

function BadgePill({ label }: { label: string }) {
  return (
    <Badge tone="neutral" size="sm" className="shrink-0 tabular-nums">
      {label}
    </Badge>
  );
}

export function SidebarLinks() {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-4">
      {groups.map((g, gi) => (
        <div key={gi} className="flex flex-col gap-0.5">
          {g.label && (
            <p className="px-2 pb-1 text-micro font-semibold uppercase tracking-wider text-fg-subtle">
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
      aria-current={active ? "page" : undefined}
      className={cn(
        "group flex items-center gap-2.5 rounded-control px-2.5 py-[7px] text-body-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
        active
          ? "bg-surface-selected text-brand"
          : "text-fg-muted hover:bg-surface-hover hover:text-fg",
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
      <div className="mt-auto rounded-card border border-success-line bg-success-soft p-2.5">
        <p className="truncate text-caption font-bold text-fg">{name}</p>
        <p className="mt-0.5 text-label leading-4 text-success-fg">
          Live business · server data only.
        </p>
      </div>
    );
  }
  if (runtimeMode === "live") {
    // Never show the seed business name in a LIVE deployment, not even while
    // the session is unresolved or the backend is unreachable.
    const note =
      status === "unreachable"
        ? "Backend unreachable · no business data shown."
        : status === "authenticated"
          ? "No business access on this account."
          : status === "anonymous"
            ? "Signed out · sign in to load a business."
            : "Checking session…";
    const unreachable = status === "unreachable";
    return (
      <div
        className={cn(
          "mt-auto rounded-card border p-2.5",
          unreachable
            ? "border-warning-line bg-warning-soft"
            : "border-dashed border-line bg-surface-sunken/60",
        )}
      >
        <p className="truncate text-caption font-bold text-fg">Live workspace</p>
        <p
          className={cn(
            "mt-0.5 text-label leading-4",
            unreachable ? "text-warning-fg" : "text-fg-subtle",
          )}
        >
          {note}
        </p>
      </div>
    );
  }

  return (
    <div className="mt-auto rounded-card border border-dashed border-line bg-surface-sunken/60 p-2.5">
      <p className="truncate text-caption font-bold text-fg">{business.name}</p>
      <p className="mt-0.5 text-label leading-4 text-fg-subtle">
        Demo business · figures & AI activity are simulated.
      </p>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden w-(--flux-sidebar-w) shrink-0 flex-col gap-4 overflow-y-auto border-r border-line bg-surface px-3 py-4 lg:flex">
      <Link href="/" className="flex items-center gap-2 px-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-cyan-500 text-white">
          <Cpu className="h-4 w-4" />
        </span>
        <span className="text-body-sm font-bold tracking-wide text-fg">
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
