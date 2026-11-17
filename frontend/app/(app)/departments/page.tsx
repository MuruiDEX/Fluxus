import Link from "next/link";
import {
  ArrowRight,
  BarChart3,
  Bot,
  Brain,
  Briefcase,
  Code2,
  Globe,
  HeartHandshake,
  Megaphone,
  MessageSquare,
  MonitorSmartphone,
  Palette,
  Plug,
  Settings2,
  ShoppingBag,
  UserPlus,
  Users,
  Video,
  Wallet,
  Workflow,
  Zap,
} from "lucide-react";
import { Badge, type BadgeTone } from "@/components/system/badges";
import { Card, PageHeader } from "@/components/system/ui";
import { cn } from "@/lib/cn";

type SurfaceStatus = "operational" | "preview" | "planned";

const STATUS_META: Record<SurfaceStatus, { label: string; tone: BadgeTone }> = {
  operational: { label: "Operational", tone: "success" },
  preview: { label: "Preview", tone: "warning" },
  planned: { label: "Planned", tone: "neutral" },
};

interface Department {
  name: string;
  focus: string;
  body: string;
  href: string;
  icon: typeof Megaphone;
  status: SurfaceStatus;
  tone: string;
}

const DEPARTMENTS: Department[] = [
  {
    name: "Marketing",
    focus: "Growth & content",
    body: "Campaign planning, SEO and content surfaces. The marketing workspace is a preview page, not a connected channel yet.",
    href: "/marketing",
    icon: Megaphone,
    status: "preview",
    tone: "bg-brand-soft text-brand",
  },
  {
    name: "Sales",
    focus: "Pipeline & revenue",
    body: "CRM, qualification and follow-up. CRM is a planned surface; agent work stays visible in AI Office and Tasks.",
    href: "/crm",
    icon: Users,
    status: "preview",
    tone: "bg-accent-soft text-accent",
  },
  {
    name: "Finance",
    focus: "Budget & forecast",
    body: "Budgets, forecasts and spend. Cost data is available in Usage and Analytics today.",
    href: "/finance",
    icon: Wallet,
    status: "preview",
    tone: "bg-holo-soft text-holo",
  },
  {
    name: "Engineering",
    focus: "Build & deploy",
    body: "Projects, task graphs and delivery agents. This department runs on the operational Projects and Agents surfaces.",
    href: "/projects",
    icon: Code2,
    status: "operational",
    tone: "bg-success-soft text-success-fg",
  },
  {
    name: "Creative",
    focus: "Design & media",
    body: "Brand identity and media generation. Brand Studio is a preview surface; no generation backend is connected.",
    href: "/brand",
    icon: Palette,
    status: "preview",
    tone: "bg-holo-soft text-holo",
  },
  {
    name: "Operations",
    focus: "Workflow & delivery",
    body: "The AI Office is operational: it shows goals, lanes, approvals and real execution state.",
    href: "/office",
    icon: Settings2,
    status: "operational",
    tone: "bg-brand-soft text-brand",
  },
  {
    name: "HR",
    focus: "Talent & agents",
    body: "Hire, activate and inspect AI employees through the Agents roster and Marketplace.",
    href: "/agents",
    icon: UserPlus,
    status: "operational",
    tone: "bg-warning-soft text-warning-fg",
  },
  {
    name: "Customer Success",
    focus: "Customers & support",
    body: "Customer records and support surfaces are planned. Agent activity remains visible in the operational activity feed.",
    href: "/customers",
    icon: HeartHandshake,
    status: "preview",
    tone: "bg-accent-soft text-accent",
  },
];

interface ModuleSurface {
  name: string;
  body: string;
  icon: typeof Bot;
  status: SurfaceStatus;
  href?: string;
}

const MODULES: ModuleSurface[] = [
  {
    name: "Plugin Marketplace",
    body: "Browse agent and pack listings from the operational Marketplace.",
    icon: ShoppingBag,
    status: "operational",
    href: "/marketplace",
  },
  {
    name: "Browser Agent",
    body: "A supervised browser worker. No browser runtime is connected.",
    icon: Globe,
    status: "planned",
  },
  {
    name: "Computer Use",
    body: "Desktop automation with explicit permissions. Design pattern only.",
    icon: MonitorSmartphone,
    status: "planned",
  },
  {
    name: "Communication Hub",
    body: "A unified inbox for external conversations. Not connected.",
    icon: MessageSquare,
    status: "planned",
  },
  {
    name: "WhatsApp Integration",
    body: "Messaging integration placeholder with a clear planned state.",
    icon: MessageSquare,
    status: "planned",
  },
  {
    name: "Creative Studio",
    body: "Brand and asset workspace preview; generation is not wired.",
    icon: Palette,
    status: "preview",
    href: "/brand",
  },
  {
    name: "Video Generation & Editing",
    body: "A future media pipeline. No render or editing backend exists.",
    icon: Video,
    status: "planned",
  },
  {
    name: "Workflow Automation",
    body: "Automation builder preview. Triggers are not connected.",
    icon: Zap,
    status: "preview",
    href: "/automations",
  },
  {
    name: "CRM & Sales",
    body: "Pipeline and revenue workspace preview.",
    icon: Briefcase,
    status: "preview",
    href: "/crm",
  },
  {
    name: "Business Intelligence",
    body: "Operational analytics over project and agent data.",
    icon: BarChart3,
    status: "operational",
    href: "/analytics",
  },
  {
    name: "Knowledge Base",
    body: "The Business Brain stores decisions, context and results.",
    icon: Brain,
    status: "operational",
    href: "/brain",
  },
  {
    name: "Agent Marketplace",
    body: "Operational listings for hiring and installing AI specialists and packs.",
    icon: Bot,
    status: "operational",
    href: "/marketplace",
  },
];

function StatusBadge({ status }: { status: SurfaceStatus }) {
  const meta = STATUS_META[status];
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export default function DepartmentsPage() {
  return (
    <>
      <PageHeader
        title="Departments"
        sub="A map of the business functions Fluxus organizes. Operational surfaces link to real workspaces; everything else is clearly marked."
      />

      <section aria-labelledby="departments-grid-title">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <h2 id="departments-grid-title" className="text-heading-sm font-bold tracking-tight text-fg">
            AI Departments
          </h2>
          <div className="flex items-center gap-2 text-micro text-fg-subtle">
            <span className="inline-flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-success-fg" /> Operational
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-warning-fg" /> Preview
            </span>
          </div>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {DEPARTMENTS.map((department) => {
            const Icon = department.icon;
            return (
              <Link
                key={department.name}
                href={department.href}
                className="flux-stat group flex h-full flex-col rounded-card p-4"
              >
                <div className="flex items-start justify-between gap-2">
                  <span
                    className={cn(
                      "inline-flex h-9 w-9 items-center justify-center rounded-[0.65rem] ring-1 ring-inset ring-brand/10",
                      department.tone,
                    )}
                  >
                    <Icon aria-hidden="true" className="h-4 w-4" />
                  </span>
                  <StatusBadge status={department.status} />
                </div>
                <p className="mt-3 text-body-sm font-bold text-fg">{department.name}</p>
                <p className="text-micro font-medium uppercase tracking-wide text-fg-subtle">
                  {department.focus}
                </p>
                <p className="mt-2 text-caption leading-5 text-fg-muted">{department.body}</p>
                <span className="mt-auto inline-flex items-center gap-1 pt-3 text-caption font-semibold text-brand">
                  Open
                  <ArrowRight
                    aria-hidden="true"
                    className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5"
                  />
                </span>
              </Link>
            );
          })}
        </div>
      </section>

      <section aria-labelledby="modules-title">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="modules-title" className="text-heading-sm font-bold tracking-tight text-fg">
              Ecosystem modules
            </h2>
            <p className="mt-0.5 text-caption text-fg-muted">
              Consistent visual patterns for future modules. Planned cards are inert by design.
            </p>
          </div>
          <Badge tone="neutral">
            <Plug aria-hidden="true" className="h-3 w-3" />
            UI only · no integrations
          </Badge>
        </div>
        <div className="mt-3 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {MODULES.map((module) => {
            const Icon = module.icon;
            const content = (
              <>
                <div className="flex items-start justify-between gap-2">
                  <span
                    className={cn(
                      "inline-flex h-8 w-8 items-center justify-center rounded-[0.6rem]",
                      module.status === "operational"
                        ? "bg-success-soft text-success-fg"
                        : module.status === "preview"
                          ? "bg-warning-soft text-warning-fg"
                          : "bg-surface-sunken text-fg-subtle",
                    )}
                  >
                    <Icon aria-hidden="true" className="h-4 w-4" />
                  </span>
                  <StatusBadge status={module.status} />
                </div>
                <p className="mt-2.5 text-caption font-bold text-fg">{module.name}</p>
                <p className="mt-1 text-micro leading-4 text-fg-muted">{module.body}</p>
                {module.href && (
                  <span className="mt-auto inline-flex items-center gap-1 pt-2 text-micro font-semibold text-brand">
                    Open
                    <ArrowRight aria-hidden="true" className="h-3 w-3" />
                  </span>
                )}
              </>
            );

            return module.href ? (
              <Link
                key={module.name}
                href={module.href}
                className="flux-stat group flex h-full flex-col rounded-card p-3"
              >
                {content}
              </Link>
            ) : (
              <div
                key={module.name}
                aria-disabled="true"
                className="flux-planned flex h-full flex-col rounded-card border border-dashed border-line bg-surface-sunken/40 p-3"
              >
                {content}
                <span className="mt-2 text-micro text-fg-subtle">Not available yet.</span>
              </div>
            );
          })}
        </div>
      </section>

      <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-start gap-3">
          <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-brand-soft text-brand">
            <Workflow aria-hidden="true" className="h-4 w-4" />
          </span>
          <div>
            <p className="text-body-sm font-bold text-fg">See the workflow that connects departments</p>
            <p className="text-caption text-fg-muted">
              The AI Office shows goals, parallel agent work, approvals and the final result.
            </p>
          </div>
        </div>
        <Link
          href="/office"
          className="inline-flex items-center gap-1 rounded-control border border-line bg-surface px-3 py-2 text-caption font-semibold text-fg transition-colors hover:border-brand/30 hover:bg-brand-soft hover:text-brand"
        >
          Open AI Office
          <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </Card>
    </>
  );
}
