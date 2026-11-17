"use client";

import { Suspense } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  BookOpen,
  Bot,
  Brain,
  Briefcase,
  Building2,
  CheckSquare,
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
import { FluxusMark } from "./FluxusMark";
import { useAgents, useApprovals, useBusiness } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgentsList, useLiveApprovals } from "@/lib/store/live";
import { runtimeMode } from "@/lib/runtime-config";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Home;
  badgeKind?: "office" | "approvals";
  /** The route exists as a clearly-labelled preview, not an operational tool. */
  planned?: boolean;
}

/**
 * Primary navigation stays short and matches the product language. Every
 * operational surface is reachable from here; previews carry a `Planned`
 * marker instead of pretending to be live.
 */
const primary: NavItem[] = [
  { href: "/", label: "Home", icon: Home },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/departments", label: "Departments", icon: Building2 },
  { href: "/integrations", label: "Plugins", icon: Plug, planned: true },
  { href: "/marketplace", label: "Marketplace", icon: ShoppingBag },
  { href: "/automations", label: "Automation", icon: Zap, planned: true },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/brain", label: "Knowledge Base", icon: BookOpen },
  { href: "/settings", label: "Settings", icon: Settings, planned: true },
];

const groups: { label: string; items: NavItem[] }[] = [
  {
    label: "Workspace",
    items: [
      { href: "/office", label: "AI Office", icon: Sparkles, badgeKind: "office" },
      { href: "/projects", label: "Projects", icon: Briefcase },
      { href: "/approvals", label: "Approvals", icon: Layers, badgeKind: "approvals" },
      { href: "/activity", label: "Activity", icon: ReceiptText },
      { href: "/usage", label: "Usage", icon: Wallet },
      { href: "/brain", label: "Business Brain", icon: Brain },
    ],
  },
  {
    label: "Business surfaces",
    items: [
      { href: "/crm", label: "CRM", icon: KanbanSquare, planned: true },
      { href: "/customers", label: "Customers", icon: Users, planned: true },
      { href: "/marketing", label: "Marketing", icon: Megaphone, planned: true },
      { href: "/finance", label: "Finance", icon: Wallet, planned: true },
      { href: "/research", label: "Research", icon: FlaskConical, planned: true },
      { href: "/brand", label: "Brand Studio", icon: Palette, planned: true },
      { href: "/website", label: "Website Studio", icon: Globe, planned: true },
      { href: "/documents", label: "Documents", icon: FileText, planned: true },
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
  return <BadgePill label={`${working}`} tone="brand" />;
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
  return <BadgePill label={String(pending)} tone="warning" />;
}

function BadgePill({
  label,
  tone,
}: {
  label: string;
  tone: "brand" | "warning" | "neutral";
}) {
  return (
    <Badge tone={tone} size="sm" className="shrink-0 px-1.5 py-0 text-micro tabular-nums">
      {label}
    </Badge>
  );
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-2.5 rounded-control px-2 py-1.5 text-body-sm font-medium transition-[background-color,color,box-shadow] duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
        active
          ? "bg-brand-soft text-fg shadow-[inset_0_1px_0_rgba(255,255,255,0.45)] ring-1 ring-inset ring-brand/20"
          : "text-fg-muted hover:bg-surface-hover hover:text-fg",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute left-0 top-1/2 h-5 w-[2.5px] -translate-y-1/2 rounded-full bg-gradient-to-b from-brand-solid to-accent transition-opacity duration-200",
          active ? "opacity-100" : "opacity-0 group-hover:opacity-50",
        )}
      />
      <span
        className={cn(
          "relative inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[0.55rem] transition-colors duration-200",
          active
            ? "bg-brand-solid text-on-brand shadow-card"
            : "bg-surface-sunken/70 text-fg-subtle group-hover:bg-surface-sunken group-hover:text-brand",
        )}
      >
        <item.icon aria-hidden="true" className="h-[15px] w-[15px]" />
      </span>
      <span className="relative min-w-0 flex-1 truncate">{item.label}</span>
      <span className="relative flex shrink-0 items-center gap-1">
        {item.badgeKind === "office" && <OfficeBadge />}
        {item.badgeKind === "approvals" && <ApprovalsBadge />}
        {item.planned && !item.badgeKind && (
          <span className="shrink-0 rounded-full border border-line bg-surface-sunken/80 px-1.5 py-0.5 text-micro font-semibold uppercase tracking-wide text-fg-subtle">
            Soon
          </span>
        )}
      </span>
    </Link>
  );
}

function NavSection({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      {label && (
        <p className="flex items-center gap-2 px-2.5 pb-1.5 text-micro font-semibold uppercase tracking-[0.16em] text-fg-subtle">
          <span className="truncate">{label}</span>
          <span aria-hidden="true" className="h-px flex-1 bg-line/70" />
        </p>
      )}
      {children}
    </div>
  );
}

export function SidebarLinks() {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);

  return (
    <nav aria-label="Main navigation" className="flex flex-col gap-4">
      <NavSection>
        {primary.map((item) => (
          <NavLink key={item.href} item={item} active={isActive(item.href)} />
        ))}
      </NavSection>
      {groups.map((group) => (
        <NavSection key={group.label} label={group.label}>
          {group.items.map((item) => (
            <NavLink
              key={`${group.label}-${item.href}-${item.label}`}
              item={item}
              active={isActive(item.href)}
            />
          ))}
        </NavSection>
      ))}
    </nav>
  );
}

function WorkspaceFooter() {
  const { status, currentBusinessId, businessNames } = useSession();
  const { business } = useBusiness();

  if (status === "authenticated" && currentBusinessId) {
    const name = businessNames[currentBusinessId] ?? `${currentBusinessId.slice(0, 8)}…`;
    return (
      <div className="flux-glass rounded-card p-2.5">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-1.5 w-1.5 shrink-0">
            <span
              aria-hidden="true"
              className="animate-pulse-dot absolute inline-flex h-full w-full rounded-full bg-success-fg opacity-60"
            />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success-fg" />
          </span>
          <p className="truncate text-caption font-bold text-fg">{name}</p>
        </div>
        <p className="mt-0.5 text-label leading-4 text-success-fg">Live business · server data</p>
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
          "rounded-card border p-2.5",
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
    <div className="flux-glass rounded-card p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
          <p className="truncate text-caption font-bold text-fg">{business.name}</p>
        </div>
        <span className="shrink-0 rounded-full bg-brand-soft px-1.5 py-0.5 text-micro font-semibold uppercase tracking-wide text-brand">
          Demo
        </span>
      </div>
      <p className="mt-0.5 text-label leading-4 text-fg-subtle">Simulated workspace · illustrative.</p>
    </div>
  );
}

export function SidebarBrand() {
  return (
    <Link
      href="/"
      className="group flex items-center gap-2.5 rounded-control px-1 py-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
    >
      <FluxusMark size={34} className="transition-transform duration-300 group-hover:scale-105" />
      <span className="min-w-0">
        <span className="block truncate text-body font-bold tracking-[0.22em] text-fg">FLUXUS</span>
        <span className="mt-0.5 block truncate text-micro font-medium uppercase tracking-[0.16em] text-fg-subtle">
          AI Business OS
        </span>
      </span>
    </Link>
  );
}

export function Sidebar() {
  return (
    <aside className="flux-sidebar relative hidden w-(--flux-sidebar-w) shrink-0 flex-col gap-4 overflow-y-auto border-r border-line/70 px-3 pb-4 pt-5 lg:flex">
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -left-16 top-16 h-52 w-52 rounded-full bg-brand/10 blur-3xl"
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-10 right-0 w-px bg-gradient-to-b from-transparent via-brand/25 to-transparent"
      />
      <SidebarBrand />
      <div className="flux-hairline h-px w-full" />
      <Suspense>
        <SidebarLinks />
      </Suspense>
      <div className="mt-auto pt-4">
        <WorkspaceFooter />
        <p className="mt-2 px-1 text-micro uppercase tracking-[0.14em] text-fg-subtle">
          Fluxus v2.0 · Enterprise
        </p>
      </div>
    </aside>
  );
}
