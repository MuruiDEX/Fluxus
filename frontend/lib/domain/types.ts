/**
 * MUSE SPARK — domain models.
 * UI consumes these via Zustand stores; services/repos abstract persistence
 * so a production backend can replace `lib/sim` without touching components.
 */

export type ID = string;

export type AgentStatus =
  | "available"
  | "thinking"
  | "working"
  | "waiting_dependency"
  | "waiting_approval"
  | "reviewing"
  | "completed"
  | "failed"
  | "escalated";

export type TaskStatus =
  | "not_started"
  | "working"
  | "waiting_dependency"
  | "waiting_assets"
  | "waiting_approval"
  | "completed"
  | "failed";

export type ApprovalStatus = "pending" | "approved" | "rejected";
export type AutopilotLevel = "manual" | "assist" | "semi" | "full";

export interface Business {
  id: ID;
  name: string;
  tagline: string;
  industry: string;
  location: string;
  ownerName: string;
  healthScore: number;
  healthBreakdown: { label: string; score: number }[];
  metrics: {
    revenue: number;
    revenueDelta: number;
    customers: number;
    customersDelta: number;
    conversion: number;
    conversionDelta: number;
    leads: number;
  };
  revenueSeries: { label: string; value: number }[];
}

export interface AgentPermissions {
  read: string[];
  write: string[];
  actions: string[];
  denied: string[];
}

export interface Agent {
  id: ID;
  businessId: ID;
  name: string;
  role: string;
  responsibilities: string[];
  tools: string[];
  permissions: AgentPermissions;
  status: AgentStatus;
  currentTask?: string;
  costPerMonth: number;
  performance: {
    completionRate: number;
    avgMinutes: number;
    approvalRate: number;
    rating: number;
  };
  hired: boolean;
  avatarHue: number;
}

export interface Task {
  id: ID;
  businessId: ID;
  projectId: ID;
  title: string;
  ownerAgentId: ID;
  status: TaskStatus;
  progress: number; // 0-100
  dependsOn: ID[];
  costAccrued: number;
  etaMinutes: number;
  summary: string;
  /** Consecutive failed/rejected attempts. >=3 escalates. */
  attempts?: number;
  /** Approval that must pass before work continues past gateAt. */
  approvalGateId?: ID;
  /** Progress % at which the gate engages (default 100). */
  gateAt?: number;
  /** Simulated cost accrued per engine tick while progressing. */
  costRate?: number;
}

export interface Project {
  id: ID;
  businessId: ID;
  title: string;
  goal: string;
  status: "planning" | "in_progress" | "review" | "completed";
  progress: number;
  budget: number;
  aiBudget: number;
  aiSpent: number;
  deadline: string;
  teamIds: ID[];
  milestones: { label: string; done: boolean }[];
  /** Stage 16 (v1.17): opt in to best-effort AI calls without a tariff. */
  allowUnpricedAi?: boolean;
}

export type UsageCostSource = "provider" | "tariff" | "unknown";
export type UsageStatus = "confirmed" | "estimated" | "unknown";
export type UsageCallKind =
  | "task_execution"
  | "brief_questions"
  | "brief_summary"
  | "plan_generation"
  | "brief_finalize";

export interface UsageRecord {
  id: ID;
  businessId: ID;
  projectId: ID | null;
  taskId: ID | null;
  agentId: ID | null;
  attempt: number;
  provider: string;
  model: string;
  callKind: UsageCallKind | null;
  providerRequestId: string | null;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  cachedTokens: number | null;
  reasoningTokens: number | null;
  /** `null` when unknown — never coerced to zero (v1.17). */
  costAmount: number | null;
  currency: string;
  costSource: UsageCostSource;
  usageStatus: UsageStatus;
  tariffVersion: string | null;
  occurredAt: string;
}

export interface UsageReservationSummary {
  active: number;
  ambiguous: number;
  consumed: number;
  released: number;
  writtenOff: number;
}

export interface UsageCallKindSummary {
  confirmedCost: number;
  estimatedCost: number;
  unknownCount: number;
}

export interface UsageSummary {
  confirmedCost: number;
  estimatedCost: number;
  unknownCount: number;
  byCallKind: Record<string, UsageCallKindSummary>;
  reservations: UsageReservationSummary;
}

export interface UsageView {
  records: UsageRecord[];
  total: number;
  summary: UsageSummary;
}

export interface Approval {
  id: ID;
  businessId: ID;
  projectId: ID;
  title: string;
  requestedBy: string;
  requestedByAgentId: ID;
  ageLabel: string;
  status: ApprovalStatus;
  what: string;
  expectedResult: string;
  cost: string;
  affects: string;
  rationale: string;
  /** Task this approval gates (engine resumes/revises it on decision). */
  gatesTaskId?: ID;
}

export interface BrainEntry {
  id: ID;
  businessId: ID;
  category:
    | "company"
    | "audience"
    | "model"
    | "brand"
    | "goals"
    | "decisions"
    | "knowledge"
    | "results";
  title: string;
  body: string;
  updatedLabel: string;
}

export interface MarketplaceAgent {
  slug: string;
  name: string;
  role: string;
  blurb: string;
  price: string;
  priceNote: string;
  rating: number;
  installs: string;
  badge?: "Popular" | "New" | "Trending";
  permissions: AgentPermissions;
  capabilities: string[];
  category: "employee" | "pack" | "automation" | "template" | "integration";
  /** LIVE only: hired into the requesting business (server-computed). */
  hired?: boolean;
  /** LIVE only: pack installed for the requesting business (server-computed). */
  installed?: boolean;
  installedVersion?: string | null;
}

export interface PackAgentPreview {
  slug: string;
  name: string;
  role: string;
  capabilities: string[];
  agentId: string | null;
  action: "create" | "reuse" | "blocked";
}

export interface PackWorkflowTask {
  key: string;
  title: string;
  ownerAgentSlug: string;
  dependsOn: string[];
}

export interface PackWorkflow {
  ref: string;
  title: string;
  goal: string;
  projectId: string | null;
  tasks: PackWorkflowTask[];
}

export interface Pack {
  slug: string;
  version: string;
  name: string;
  description: string;
  blurb: string;
  price: string;
  priceNote: string;
  rating: number;
  installs: string;
  installed: boolean;
  installedVersion: string | null;
  installedAt: string | null;
  agents: PackAgentPreview[];
  workflow: PackWorkflow | null;
}

export interface PackPreview {
  pack: Pack;
  canInstall: boolean;
  blockers: { code: string; message: string }[];
}

export interface ActivityEvent {
  id: ID;
  at: string;
  agentName: string;
  text: string;
  kind: "work" | "approval" | "system" | "result";
  cost?: number;
}

export interface Brief {
  goal: string;
  country?: string;
  model?: string;
  budget?: string;
  audience?: string;
  summary: string[];
}
