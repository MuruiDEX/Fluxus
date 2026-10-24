/**
 * Wire → domain mapping for LIVE reads (Stage 7.3).
 *
 * Backend shapes (contract §1) map onto the existing domain models so LIVE
 * screens reuse the same components as DEMO. Money arrives as JSON strings
 * ("0.00"); parsed to numbers here so `.toFixed()`/formatting never breaks.
 * `dependsOn` rides the wire since contract amendment v1.10 (Stage 8.4).
 */

import { parseMoney, type ApiActivityEvent, type ApiAgent, type ApiApproval, type ApiBrainEntry, type ApiBusiness, type ApiMarketplaceEntry, type ApiPack, type ApiPackPreview, type ApiProject, type ApiTask, type ApiUsagePage, type ApiUsageRecord } from "@/lib/api";
import type { ActivityEvent, Agent, Approval, BrainEntry, Business, MarketplaceAgent, Pack, PackPreview, Project, Task, UsageRecord, UsageView } from "@/lib/domain/types";

export function toDomainBusiness(b: ApiBusiness): Business {
  return {
    id: b.id,
    name: b.name,
    tagline: b.tagline,
    industry: b.industry,
    location: b.location,
    ownerName: b.ownerName,
    healthScore: b.healthScore,
    healthBreakdown: b.healthBreakdown.map((item) => ({ ...item })),
    metrics: {
      revenue: b.metrics.revenue ?? 0,
      revenueDelta: b.metrics.revenueDelta ?? 0,
      customers: b.metrics.customers ?? 0,
      customersDelta: b.metrics.customersDelta ?? 0,
      conversion: b.metrics.conversion ?? 0,
      conversionDelta: b.metrics.conversionDelta ?? 0,
      leads: b.metrics.leads ?? 0,
    },
    revenueSeries: b.revenueSeries.map((point) => ({ ...point })),
    aiBudget: parseMoney(b.aiBudget ?? "0"),
    allowUnpricedAi: b.allowUnpricedAi ?? false,
  };
}

export function toDomainProject(p: ApiProject): Project {
  return {
    id: p.id,
    businessId: p.businessId,
    title: p.title,
    goal: p.goal,
    status: p.status,
    progress: p.progress,
    budget: parseMoney(p.budget),
    aiBudget: parseMoney(p.aiBudget),
    aiSpent: parseMoney(p.aiSpent),
    deadline: p.deadline,
    teamIds: [...p.teamIds],
    milestones: p.milestones.map((m) => ({ label: m.label, done: m.done })),
    allowUnpricedAi: p.allowUnpricedAi ?? false,
  };
}

export function toDomainUsageRecord(r: ApiUsageRecord): UsageRecord {
  return {
    ...r,
    costAmount: r.costAmount == null ? null : parseMoney(r.costAmount),
  };
}

export function toDomainUsage(page: ApiUsagePage): UsageView {
  return {
    records: page.items.map(toDomainUsageRecord),
    total: page.total,
    summary: {
      confirmedCost: parseMoney(page.summary.confirmedCost),
      estimatedCost: parseMoney(page.summary.estimatedCost),
      unknownCount: page.summary.unknownCount,
      byCallKind: Object.fromEntries(
        Object.entries(page.summary.byCallKind ?? {}).map(([kind, item]) => [
          kind,
          {
            confirmedCost: parseMoney(item.confirmedCost),
            estimatedCost: parseMoney(item.estimatedCost),
            unknownCount: item.unknownCount,
          },
        ]),
      ),
      reservations: {
        active: parseMoney(page.summary.reservations.active),
        ambiguous: parseMoney(page.summary.reservations.ambiguous),
        consumed: parseMoney(page.summary.reservations.consumed),
        released: parseMoney(page.summary.reservations.released),
        writtenOff: parseMoney(page.summary.reservations.writtenOff),
      },
    },
  };
}

export function toDomainTask(t: ApiTask): Task {
  return {
    id: t.id,
    businessId: t.businessId,
    projectId: t.projectId,
    title: t.title,
    ownerAgentId: t.ownerAgentId,
    status: t.status,
    progress: t.progress,
    dependsOn: [...(t.dependsOn ?? [])],
    costAccrued: parseMoney(t.costAccrued),
    etaMinutes: t.etaMinutes,
    summary: t.summary,
    attempts: t.attempts ?? undefined,
    approvalGateId: t.approvalGateId ?? undefined,
    gateAt: t.gateAt ?? undefined,
    costRate: t.costRate == null ? undefined : parseMoney(t.costRate),
  };
}

export function toDomainAgent(a: ApiAgent): Agent {
  return {
    id: a.id,
    businessId: a.businessId,
    name: a.name,
    role: a.role,
    responsibilities: [...a.responsibilities],
    tools: [...a.tools],
    permissions: {
      read: a.permissions.read ?? [],
      write: a.permissions.write ?? [],
      actions: a.permissions.actions ?? [],
      denied: a.permissions.denied ?? [],
    },
    status: a.status,
    currentTask: a.currentTask ?? undefined,
    costPerMonth: a.costPerMonth,
    performance: {
      completionRate: a.performance.completionRate ?? 0,
      avgMinutes: a.performance.avgMinutes ?? 0,
      approvalRate: a.performance.approvalRate ?? 0,
      rating: a.performance.rating ?? 0,
    },
    hired: a.hired,
    avatarHue: a.avatarHue,
  };
}

export function toDomainApproval(a: ApiApproval): Approval {
  return {
    id: a.id,
    businessId: a.businessId,
    projectId: a.projectId,
    title: a.title,
    requestedBy: a.requestedBy,
    requestedByAgentId: a.requestedByAgentId,
    ageLabel: a.ageLabel,
    status: a.status,
    what: a.what,
    expectedResult: a.expectedResult,
    cost: a.cost,
    affects: a.affects,
    rationale: a.rationale,
    gatesTaskId: a.gatesTaskId ?? undefined,
  };
}

export function toDomainActivityEvent(e: ApiActivityEvent): ActivityEvent {
  return {
    id: e.id,
    // Wire carries the RFC 3339 server timestamp (§4); display strings are
    // derived client-side (see lib/live/datetime.ts), never stored.
    at: e.at,
    agentName: e.agentName,
    text: e.text,
    kind: e.kind,
    cost: e.cost == null ? undefined : parseMoney(e.cost),
  };
}

export function toDomainBrainEntry(e: ApiBrainEntry): BrainEntry {
  return {
    id: e.id,
    businessId: e.businessId,
    category: e.category,
    title: e.title,
    body: e.body,
    updatedLabel: e.updatedLabel,
  };
}

export function toDomainMarketplaceEntry(e: ApiMarketplaceEntry): MarketplaceAgent {
  return {
    slug: e.slug,
    name: e.name,
    role: e.role,
    blurb: e.blurb,
    price: e.price,
    priceNote: e.priceNote,
    rating: e.rating,
    installs: e.installs,
    badge: (e.badge ?? undefined) as MarketplaceAgent["badge"],
    permissions: {
      read: e.permissions.read ?? [],
      write: e.permissions.write ?? [],
      actions: e.permissions.actions ?? [],
      denied: e.permissions.denied ?? [],
    },
    capabilities: [...e.capabilities],
    category: e.category as MarketplaceAgent["category"],
    hired: e.hired,
    installed: e.installed,
    installedVersion: e.installedVersion,
  };
}

export function toDomainPack(p: ApiPack): Pack {
  return {
    slug: p.slug,
    version: p.version,
    name: p.name,
    description: p.description,
    blurb: p.blurb,
    price: p.price,
    priceNote: p.priceNote,
    rating: p.rating,
    installs: p.installs,
    installed: p.installed,
    installedVersion: p.installedVersion,
    installedAt: p.installedAt,
    agents: p.agents.map((a) => ({ ...a, capabilities: [...a.capabilities] })),
    workflow: p.workflow
      ? {
          ...p.workflow,
          tasks: p.workflow.tasks.map((t) => ({ ...t, dependsOn: [...t.dependsOn] })),
        }
      : null,
  };
}

export function toDomainPackPreview(p: ApiPackPreview): PackPreview {
  return {
    pack: toDomainPack(p.pack),
    canInstall: p.canInstall,
    blockers: p.blockers.map((b) => ({ ...b })),
  };
}
