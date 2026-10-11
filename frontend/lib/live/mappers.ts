/**
 * Wire → domain mapping for LIVE reads (Stage 7.3).
 *
 * Backend shapes (contract §1) map onto the existing domain models so LIVE
 * screens reuse the same components as DEMO. Two honest gaps, both by design:
 * - `dependsOn`: the backend stores dependencies but exposes no endpoint yet,
 *   so LIVE tasks carry an empty list (never invented edges).
 * - Money arrives as JSON strings ("0.00"); parsed to numbers here so
 *   `.toFixed()`/formatting never breaks.
 */

import { parseMoney, type ApiActivityEvent, type ApiAgent, type ApiApproval, type ApiBrainEntry, type ApiProject, type ApiTask } from "@/lib/api";
import type { ActivityEvent, Agent, Approval, BrainEntry, Project, Task } from "@/lib/domain/types";

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
    dependsOn: [],
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
