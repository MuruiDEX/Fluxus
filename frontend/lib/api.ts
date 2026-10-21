/**
 * Backend API client (Stage 6 frontend slice).
 *
 * - Base URL is the build-time `NEXT_PUBLIC_API_URL` origin in LIVE mode.
 *   There is no production loopback fallback; DEMO mode never contacts it.
 * - Session cookie is HttpOnly: the browser sends it automatically, so we
 *   only set `credentials: "include"`. No session id, password, or CSRF
 *   secret ever touches localStorage — CSRF lives in memory only.
 * - Mutations carry the in-memory `X-CSRF-Token`; 401s notify the session
 *   store through `onUnauthorized` (wired by the auth store, no imports).
 */

import {
  backendRequestsEnabled,
  readRuntimeEnv,
  resolveApiOrigin,
} from "@/lib/runtime-config";

export interface ApiErrorDetails {
  status: number;
  code: string;
  message: string;
  /**
   * Raw error-response body when the server sent one. Execution endpoints
   * answer 4xx/5xx WITH an outcome payload (already_running, refused,
   * unknown) — callers recover it from here instead of losing it to the throw.
   */
  payload?: unknown;
}

export class ApiError extends Error {
  status: number;
  code: string;
  payload?: unknown;

  constructor({ status, code, message, payload }: ApiErrorDetails) {
    super(message);
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function onUnauthorized(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

export function apiBaseUrl(): string {
  return resolveApiOrigin(readRuntimeEnv()) ?? "";
}

export interface ApiUser {
  id: string;
  email: string;
  displayName: string;
}

export function isAbortError(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === "AbortError";
}

export async function api<T>(
  path: string,
  init?: { method?: string; body?: unknown; signal?: AbortSignal },
): Promise<T> {
  if (!backendRequestsEnabled()) {
    throw new ApiError({
      status: 0,
      code: "demo_mode_blocked",
      message: "Backend requests are disabled in demo mode.",
    });
  }
  const method = init?.method ?? "GET";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Client-Contract": "1",
  };
  if (UNSAFE.has(method) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, {
      method,
      headers,
      credentials: "include",
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init?.signal,
    });
  } catch (cause) {
    // Abandoned requests are not failures: rethrow so callers can ignore them.
    if (isAbortError(cause)) throw cause;
    throw new ApiError({ status: 0, code: "network_unreachable", message: String(cause) });
  }
  if (res.status === 401) {
    setCsrfToken(null);
    unauthorizedHandler?.();
  }
  if (!res.ok) {
    let code = "http_error";
    let message = `Request failed with status ${res.status}.`;
    let payload: unknown;
    try {
      const data = await res.json();
      payload = data;
      if (data?.error?.code) code = String(data.error.code);
      if (data?.error?.message) message = String(data.error.message);
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new ApiError({ status: res.status, code, message, payload });
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function apiLogin(email: string, password: string) {
  const data = await api<{ user: ApiUser; csrf_token: string; businesses: string[] }>(
    "/api/v1/auth/login",
    { method: "POST", body: { email, password } },
  );
  setCsrfToken(data.csrf_token);
  return data;
}

export async function apiLogout() {
  try {
    await api("/api/v1/auth/logout", { method: "POST" });
  } finally {
    setCsrfToken(null);
  }
}

export async function apiMe() {
  const data = await api<{ user: ApiUser; csrf_token: string; businesses: string[] }>(
    "/api/v1/auth/me",
  );
  // Restore the session-bound token after full page loads (memory-only copy
  // is wiped by navigation; the server re-issues the same value).
  if (data.csrf_token) setCsrfToken(data.csrf_token);
  return data;
}

// ---------------------------------------------------------------------------
// Stage 7.1 wire types — mirror of backend/app/api/v1/schemas.py (camelCase).
// Money arrives as JSON strings ("0.00", see Money/parseMoney); `at` is an
// RFC 3339 timestamp string (§4 — display strings are derived client-side).
// ---------------------------------------------------------------------------

export type Money = string | number;

export function parseMoney(value: Money): number {
  return typeof value === "number" ? value : Number(value);
}

export function parseIsoDate(value: string): Date | null {
  if (!value) return null;
  const time = Date.parse(value);
  return Number.isNaN(time) ? null : new Date(time);
}

export type TaskStatus =
  | "not_started"
  | "working"
  | "waiting_dependency"
  | "waiting_assets"
  | "waiting_approval"
  | "completed"
  | "failed";

export type ProjectStatus = "planning" | "in_progress" | "review" | "completed";

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

export type ApprovalStatus = "pending" | "approved" | "rejected";

export type BrainCategory =
  | "company"
  | "audience"
  | "model"
  | "brand"
  | "goals"
  | "decisions"
  | "knowledge"
  | "results";

export type ActivityKind = "work" | "approval" | "system" | "result";

export interface ApiBusiness {
  id: string;
  name: string;
  tagline: string;
  industry: string;
  location: string;
  ownerName: string;
  healthScore: number;
  healthBreakdown: { label: string; score: number }[];
  metrics: Record<string, number>;
  revenueSeries: { label: string; value: number }[];
  contract: string;
}

export interface ApiProject {
  id: string;
  businessId: string;
  title: string;
  goal: string;
  status: ProjectStatus;
  progress: number;
  budget: Money;
  aiBudget: Money;
  aiSpent: Money;
  deadline: string;
  teamIds: string[];
  milestones: { label: string; done: boolean }[];
  /** Stage 16 (v1.17): opt in to best-effort AI calls without a tariff. */
  allowUnpricedAi?: boolean;
  contract: string;
}

export interface ApiTask {
  id: string;
  businessId: string;
  projectId: string;
  title: string;
  ownerAgentId: string;
  status: TaskStatus;
  progress: number;
  /** Stored dependency edges (contract §1; v1.10 — older servers omit it). */
  dependsOn?: string[];
  costAccrued: Money;
  etaMinutes: number;
  summary: string;
  attempts: number | null;
  approvalGateId: string | null;
  gateAt: number | null;
  costRate: Money | null;
  contract: string;
}

export interface ApiAgent {
  id: string;
  businessId: string;
  name: string;
  role: string;
  responsibilities: string[];
  tools: string[];
  permissions: Record<string, string[]>;
  status: AgentStatus;
  currentTask: string | null;
  costPerMonth: number;
  performance: Record<string, number>;
  hired: boolean;
  avatarHue: number;
  contract: string;
}

export interface ApiApproval {
  id: string;
  businessId: string;
  projectId: string;
  title: string;
  requestedBy: string;
  requestedByAgentId: string;
  ageLabel: string;
  status: ApprovalStatus;
  what: string;
  expectedResult: string;
  cost: string;
  affects: string;
  rationale: string;
  gatesTaskId: string | null;
  contract: string;
}

export interface ApiActivityEvent {
  id: string;
  at: string;
  agentName: string;
  text: string;
  kind: ActivityKind;
  cost: Money | null;
  contract: string;
}

export interface ApiBrainEntry {
  id: string;
  businessId: string;
  category: BrainCategory;
  title: string;
  body: string;
  updatedLabel: string;
  contract: string;
}

export interface ApiPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  contract: string;
}

export type PageParams = {
  limit?: number;
  offset?: number;
};

export type ActivityFilter = PageParams & {
  projectId?: string;
};

export type BrainFilter = PageParams & {
  category?: BrainCategory;
};

export type UsageCostSource = "provider" | "tariff" | "unknown";
export type UsageStatus = "confirmed" | "estimated" | "unknown";
export type UsageCallKind =
  | "task_execution"
  | "brief_questions"
  | "brief_summary"
  | "plan_generation"
  | "brief_finalize";

export interface ApiUsageRecord {
  id: string;
  businessId: string;
  projectId: string | null;
  taskId: string | null;
  agentId: string | null;
  attempt: number;
  provider: string;
  model: string;
  /** `null` for legacy rows written before the column existed (v1.18). */
  callKind: UsageCallKind | null;
  providerRequestId: string | null;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  cachedTokens: number | null;
  reasoningTokens: number | null;
  /** `null` when the cost is unknown — never coerced to zero (v1.17). */
  costAmount: Money | null;
  currency: string;
  costSource: UsageCostSource;
  usageStatus: UsageStatus;
  tariffVersion: string | null;
  occurredAt: string;
}

export interface ApiUsageReservationSummary {
  active: Money;
  ambiguous: Money;
  consumed: Money;
  released: Money;
  writtenOff: Money;
}

export interface ApiUsageCallKindSummary {
  confirmedCost: Money;
  estimatedCost: Money;
  unknownCount: number;
}

export interface ApiUsageSummary {
  confirmedCost: Money;
  estimatedCost: Money;
  unknownCount: number;
  /** Per-call-kind money split; NULL kinds are keyed by "" (v1.18). */
  byCallKind: Record<string, ApiUsageCallKindSummary>;
  reservations: ApiUsageReservationSummary;
}

export interface ApiUsagePage extends ApiPage<ApiUsageRecord> {
  summary: ApiUsageSummary;
}

export type UsageFilter = PageParams & {
  projectId?: string;
  taskId?: string;
};

function pageQuery(params?: PageParams & Record<string, string | number | undefined>): string {
  if (!params) return "";
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  for (const [key, value] of Object.entries(params)) {
    if (key === "limit" || key === "offset" || value === undefined) continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}

const id = encodeURIComponent;

export interface RequestOpts {
  signal?: AbortSignal;
}

export async function apiGetBusiness(businessId: string, opts?: RequestOpts) {
  return api<ApiBusiness>(`/api/v1/businesses/${id(businessId)}`, { signal: opts?.signal });
}

export async function apiListProjects(businessId: string, params?: PageParams, opts?: RequestOpts) {
  return api<ApiPage<ApiProject>>(
    `/api/v1/businesses/${id(businessId)}/projects${pageQuery(params)}`,
    { signal: opts?.signal },
  );
}

export async function apiGetProject(businessId: string, projectId: string, opts?: RequestOpts) {
  return api<ApiProject>(`/api/v1/businesses/${id(businessId)}/projects/${id(projectId)}`, {
    signal: opts?.signal,
  });
}

export async function apiListTasks(
  businessId: string,
  projectId: string,
  params?: PageParams,
  opts?: RequestOpts,
) {
  return api<ApiPage<ApiTask>>(
    `/api/v1/businesses/${id(businessId)}/projects/${id(projectId)}/tasks${pageQuery(params)}`,
    { signal: opts?.signal },
  );
}

export async function apiGetTask(
  businessId: string,
  projectId: string,
  taskId: string,
  opts?: RequestOpts,
) {
  return api<ApiTask>(
    `/api/v1/businesses/${id(businessId)}/projects/${id(projectId)}/tasks/${id(taskId)}`,
    { signal: opts?.signal },
  );
}

export async function apiListAgents(businessId: string, params?: PageParams, opts?: RequestOpts) {
  return api<ApiPage<ApiAgent>>(`/api/v1/businesses/${id(businessId)}/agents${pageQuery(params)}`, {
    signal: opts?.signal,
  });
}

export async function apiGetAgent(businessId: string, agentId: string, opts?: RequestOpts) {
  return api<ApiAgent>(`/api/v1/businesses/${id(businessId)}/agents/${id(agentId)}`, {
    signal: opts?.signal,
  });
}

export async function apiListApprovals(
  businessId: string,
  params?: PageParams,
  opts?: RequestOpts,
) {
  return api<ApiPage<ApiApproval>>(
    `/api/v1/businesses/${id(businessId)}/approvals${pageQuery(params)}`,
    { signal: opts?.signal },
  );
}

export async function apiGetApproval(businessId: string, approvalId: string, opts?: RequestOpts) {
  return api<ApiApproval>(`/api/v1/businesses/${id(businessId)}/approvals/${id(approvalId)}`, {
    signal: opts?.signal,
  });
}

// ---------------------------------------------------------------------------
// Stage 7.7 decisions — mirrors DecisionResponse (verified outcomes only).
// ---------------------------------------------------------------------------

export type DecisionOutcome =
  | "approved"
  | "already_approved"
  | "rejected"
  | "already_rejected"
  | "conflict";

export interface ApiDecision {
  outcome: DecisionOutcome;
  approvalId: string;
  status: string | null;
  contract: string;
}

export function isDecisionOutcome(value: unknown): value is DecisionOutcome {
  return (
    value === "approved" ||
    value === "already_approved" ||
    value === "rejected" ||
    value === "already_rejected" ||
    value === "conflict"
  );
}

/** Outcome carried inside the 409 answer (recovered from ApiError.payload). */
export function decisionOutcomeOf(payload: unknown): DecisionOutcome | null {
  if (typeof payload !== "object" || payload === null) return null;
  const outcome = (payload as { outcome?: unknown }).outcome;
  return isDecisionOutcome(outcome) ? outcome : null;
}

export async function apiApproveApproval(
  businessId: string,
  approvalId: string,
  opts?: RequestOpts,
) {
  return api<ApiDecision>(
    `/api/v1/businesses/${id(businessId)}/approvals/${id(approvalId)}/approve`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

export async function apiRejectApproval(
  businessId: string,
  approvalId: string,
  opts?: RequestOpts,
) {
  return api<ApiDecision>(
    `/api/v1/businesses/${id(businessId)}/approvals/${id(approvalId)}/reject`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

export async function apiListActivity(
  businessId: string,
  filter?: ActivityFilter,
  opts?: RequestOpts,
) {
  const params: PageParams & Record<string, string | number | undefined> = {
    limit: filter?.limit,
    offset: filter?.offset,
    project_id: filter?.projectId,
  };
  return api<ApiPage<ApiActivityEvent>>(
    `/api/v1/businesses/${id(businessId)}/activity${pageQuery(params)}`,
    { signal: opts?.signal },
  );
}

export async function apiListBrain(businessId: string, filter?: BrainFilter, opts?: RequestOpts) {
  const params: PageParams & Record<string, string | number | undefined> = {
    limit: filter?.limit,
    offset: filter?.offset,
    category: filter?.category,
  };
  return api<ApiPage<ApiBrainEntry>>(
    `/api/v1/businesses/${id(businessId)}/brain${pageQuery(params)}`,
    { signal: opts?.signal },
  );
}

export async function apiListUsage(
  businessId: string,
  filter?: UsageFilter,
  opts?: RequestOpts,
) {
  const params: PageParams & Record<string, string | number | undefined> = {
    limit: filter?.limit,
    offset: filter?.offset,
    project_id: filter?.projectId,
    task_id: filter?.taskId,
  };
  return api<ApiUsagePage>(`/api/v1/businesses/${id(businessId)}/usage${pageQuery(params)}`, {
    signal: opts?.signal,
  });
}

// ---------------------------------------------------------------------------
// Stage 7.4 execution — mirrors ExecutionResponse (verified outcomes only).
// ---------------------------------------------------------------------------

export type ExecutionOutcome =
  | "completed"
  | "failed"
  | "already_completed"
  | "already_running"
  | "refused"
  | "unknown";

export interface ApiExecutionError {
  code: string;
  message: string;
}

export interface ApiExecution {
  outcome: ExecutionOutcome;
  taskId: string;
  status: string | null;
  attempts: number | null;
  llmAttempts: number;
  usage: Record<string, unknown>;
  error: ApiExecutionError | null;
  contract: string;
}

export interface RunExecutionOpts extends RequestOpts {
  /** Per-call LLM budget in seconds; the server clamps to its own maximum. */
  timeoutS?: number;
}

export function isExecutionOutcome(value: unknown): value is ExecutionOutcome {
  return (
    value === "completed" ||
    value === "failed" ||
    value === "already_completed" ||
    value === "already_running" ||
    value === "refused" ||
    value === "unknown"
  );
}

/** Outcome carried inside 4xx/5xx execution answers (recovered from ApiError.payload). */
export function executionOutcomeOf(payload: unknown): ExecutionOutcome | null {
  if (typeof payload !== "object" || payload === null) return null;
  const outcome = (payload as { outcome?: unknown }).outcome;
  return isExecutionOutcome(outcome) ? outcome : null;
}

export function executionErrorOf(payload: unknown): ApiExecutionError | null {
  if (typeof payload !== "object" || payload === null) return null;
  const error = (payload as { error?: unknown }).error;
  if (typeof error !== "object" || error === null) return null;
  const { code, message } = error as { code?: unknown; message?: unknown };
  if (typeof code !== "string" || typeof message !== "string") return null;
  return { code, message };
}

export async function apiRunExecution(
  businessId: string,
  projectId: string,
  taskId: string,
  opts?: RunExecutionOpts,
) {
  const body: Record<string, unknown> = {};
  if (opts?.timeoutS !== undefined) body.timeout_s = opts.timeoutS;
  return api<ApiExecution>(
    `/api/v1/businesses/${id(businessId)}/projects/${id(projectId)}/tasks/${id(taskId)}/executions`,
    { method: "POST", body, signal: opts?.signal },
  );
}

// ---------------------------------------------------------------------------
// Stage 8.4 plan driver — brief resume, dependency edges, stale-run recovery.
// ---------------------------------------------------------------------------

export type BriefStatus =
  | "draft"
  | "questions_ready"
  | "brief_ready"
  | "planned"
  | "running"
  | "completed"
  | "failed";

export interface ApiBrief {
  id: string;
  businessId: string;
  goal: string;
  status: BriefStatus;
  questions: { id: string; text: string; options?: string[] }[];
  answers: unknown[];
  summary: Record<string, unknown> | null;
  projectId: string | null;
  /** Stage 8.5 finalization envelope; null until the brief completes. */
  finalReport?: {
    project_id: string;
    report: ApiFinalReport;
    learnings: ApiFinalLearning[];
    costs: ApiFinalCosts;
  } | null;
  contract: string;
}

export async function apiGetBrief(businessId: string, briefId: string, opts?: RequestOpts) {
  return api<ApiBrief>(`/api/v1/businesses/${id(businessId)}/briefs/${id(briefId)}`, {
    signal: opts?.signal,
  });
}

// ---------------------------------------------------------------------------
// Stage 11 intake — goal → questions → answers → brief → plan. Answers use
// the backend's snake_case wire names (BriefAnswerItem has no camel alias).
// ---------------------------------------------------------------------------

export async function apiCreateBrief(
  businessId: string,
  goal: string,
  opts?: RequestOpts,
) {
  return api<ApiBrief>(`/api/v1/businesses/${id(businessId)}/briefs`, {
    method: "POST",
    body: { goal },
    signal: opts?.signal,
  });
}

export interface ApiBriefAnswer {
  question_id: string;
  text: string;
}

export async function apiAnswerBrief(
  businessId: string,
  briefId: string,
  answers: ApiBriefAnswer[],
  opts?: RequestOpts,
) {
  return api<ApiBrief>(`/api/v1/businesses/${id(businessId)}/briefs/${id(briefId)}/answers`, {
    method: "POST",
    body: { answers },
    signal: opts?.signal,
  });
}

export async function apiPlanBrief(businessId: string, briefId: string, opts?: RequestOpts) {
  return api<ApiPlan>(
    `/api/v1/businesses/${id(businessId)}/briefs/${id(briefId)}/plan`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

export interface ApiPlan {
  briefId: string;
  project: ApiProject;
  tasks: ApiTask[];
  approvals: ApiApproval[];
  contract: string;
}

// ---------------------------------------------------------------------------
// Stage 8.5 finalization — mirrors FinalizeResponse (verified shapes only).
// ---------------------------------------------------------------------------

export interface ApiFinalTaskEntry {
  id: string;
  title: string;
  summary: string;
}

export interface ApiFinalFailedEntry {
  id: string;
  title: string;
  reason: string;
}

export interface ApiFinalReport {
  goal: string;
  completed_tasks: ApiFinalTaskEntry[];
  failed_tasks: ApiFinalFailedEntry[];
  limitations: string[];
  recommendations: string[];
}

export interface ApiFinalLearning {
  id: string;
  category: string;
  title: string;
  body: string;
}

export interface ApiFinalCosts {
  budget: string;
  ai_budget: string;
  ai_spent: string;
  /** Confirmed ledger snapshot also reports estimated cost separately (v1.18). */
  ai_estimated?: string;
  note: string;
}

export interface ApiFinalize {
  briefId: string;
  projectId: string;
  status: string;
  report: ApiFinalReport;
  learnings: ApiFinalLearning[];
  costs: ApiFinalCosts;
  contract: string;
}

export async function apiFinalizeBrief(
  businessId: string,
  briefId: string,
  opts?: RequestOpts,
) {
  return api<ApiFinalize>(
    `/api/v1/businesses/${id(businessId)}/briefs/${id(briefId)}/finalize`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

export type RecoveryOutcome = "recovered" | "not_stale" | "not_found";

export interface ApiRecovery {
  outcome: RecoveryOutcome;
  taskId: string;
  status: string | null;
  contract: string;
}

export function isRecoveryOutcome(value: unknown): value is RecoveryOutcome {
  return value === "recovered" || value === "not_stale" || value === "not_found";
}

/**
 * Outcome carried inside the 409 answer (recovered from ApiError.payload).
 * The recovery endpoint answers 409 with a RecoveryResponse body — NOT the
 * error envelope — when the row exists but does not qualify.
 */
export function recoveryOutcomeOf(payload: unknown): RecoveryOutcome | null {
  if (typeof payload !== "object" || payload === null) return null;
  const outcome = (payload as { outcome?: unknown }).outcome;
  return isRecoveryOutcome(outcome) ? outcome : null;
}

export async function apiRecoverTask(
  businessId: string,
  projectId: string,
  taskId: string,
  opts?: RequestOpts,
) {
  return api<ApiRecovery>(
    `/api/v1/businesses/${id(businessId)}/projects/${id(projectId)}/tasks/${id(taskId)}/recover`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

// ---------------------------------------------------------------------------
// Stage 8 marketplace — mirrors MarketplaceResponse (verified shapes only).
// `hired` is computed per requesting business; the catalog itself is global.
// ---------------------------------------------------------------------------

export interface ApiMarketplaceEntry {
  slug: string;
  name: string;
  role: string;
  blurb: string;
  price: string;
  priceNote: string;
  rating: number;
  installs: string;
  badge: string | null;
  capabilities: string[];
  permissions: Record<string, string[]>;
  category: string;
  hired: boolean;
  installed: boolean;
  installedVersion: string | null;
  contract: string;
}

export async function apiListMarketplace(
  businessId: string,
  params?: PageParams,
  opts?: RequestOpts,
) {
  return api<ApiPage<ApiMarketplaceEntry>>(
    `/api/v1/businesses/${id(businessId)}/marketplace${pageQuery(params)}`,
    { signal: opts?.signal },
  );
}

export async function apiGetListing(businessId: string, slug: string, opts?: RequestOpts) {
  return api<ApiMarketplaceEntry>(
    `/api/v1/businesses/${id(businessId)}/marketplace/${id(slug)}`,
    { signal: opts?.signal },
  );
}

/** Hire an employee listing: 201 answers the created ApiAgent in scope. */
export async function apiHireAgent(businessId: string, slug: string, opts?: RequestOpts) {
  return api<ApiAgent>(
    `/api/v1/businesses/${id(businessId)}/marketplace/${id(slug)}/hire`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

// ---------------------------------------------------------------------------
// Stage 15 packs - server manifests, preview blockers, atomic install.
// Install is idempotent per version: 201 installed, 200 already_installed;
// a different installed version is 409 pack_version_conflict.
// ---------------------------------------------------------------------------

export interface ApiPackAgent {
  slug: string;
  name: string;
  role: string;
  capabilities: string[];
  agentId: string | null;
  action: "create" | "reuse" | "blocked";
}

export interface ApiPackWorkflowTask {
  key: string;
  title: string;
  ownerAgentSlug: string;
  dependsOn: string[];
}

export interface ApiPackWorkflow {
  ref: string;
  title: string;
  goal: string;
  projectId: string | null;
  tasks: ApiPackWorkflowTask[];
}

export interface ApiPack {
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
  agents: ApiPackAgent[];
  workflow: ApiPackWorkflow | null;
  contract: string;
}

export interface ApiPackBlocker {
  code: string;
  message: string;
}

export interface ApiPackPreview {
  pack: ApiPack;
  canInstall: boolean;
  blockers: ApiPackBlocker[];
  contract: string;
}

export interface ApiPackInstall {
  outcome: "installed" | "already_installed";
  pack: ApiPack;
  installationId: string;
  installedAt: string;
  agents: ApiPackAgent[];
  project: ApiProject | null;
  tasks: ApiTask[];
  contract: string;
}

export async function apiListPacks(businessId: string, opts?: RequestOpts) {
  return api<ApiPage<ApiPack>>(`/api/v1/businesses/${id(businessId)}/packs`, {
    signal: opts?.signal,
  });
}

export async function apiGetPack(businessId: string, slug: string, opts?: RequestOpts) {
  return api<ApiPack>(`/api/v1/businesses/${id(businessId)}/packs/${id(slug)}`, {
    signal: opts?.signal,
  });
}

export async function apiPreviewPack(businessId: string, slug: string, opts?: RequestOpts) {
  return api<ApiPackPreview>(
    `/api/v1/businesses/${id(businessId)}/packs/${id(slug)}/preview`,
    { signal: opts?.signal },
  );
}

export async function apiInstallPack(businessId: string, slug: string, opts?: RequestOpts) {
  return api<ApiPackInstall>(
    `/api/v1/businesses/${id(businessId)}/packs/${id(slug)}/install`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

// ---------------------------------------------------------------------------
// Stage 14 agent lifecycle — activate/disabled is the `hired` flag.
// Idempotent 200s replays the current row; foreign/missing is uniform 404.
// ---------------------------------------------------------------------------

export async function apiActivateAgent(
  businessId: string,
  agentId: string,
  opts?: RequestOpts,
) {
  return api<ApiAgent>(
    `/api/v1/businesses/${id(businessId)}/agents/${id(agentId)}/activate`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

export async function apiDisableAgent(
  businessId: string,
  agentId: string,
  opts?: RequestOpts,
) {
  return api<ApiAgent>(
    `/api/v1/businesses/${id(businessId)}/agents/${id(agentId)}/disable`,
    { method: "POST", body: {}, signal: opts?.signal },
  );
}

// ---------------------------------------------------------------------------
// Stage 7.1 management writes — manual Brain entries + metadata patches.
// Mutations: CSRF token is attached automatically, LIVE gating stays in the
// stores (session + membership); these functions never touch DEMO state.
// Executor/derived fields are not part of the input types by construction.
// ---------------------------------------------------------------------------

export interface BrainCreateInput {
  category: string;
  title: string;
  body?: string;
}

export async function apiCreateBrain(
  businessId: string,
  input: BrainCreateInput,
  opts?: RequestOpts,
) {
  return api<ApiBrainEntry>(`/api/v1/businesses/${id(businessId)}/brain`, {
    method: "POST",
    body: input,
    signal: opts?.signal,
  });
}

export interface ProjectPatchInput {
  title?: string;
  goal?: string;
  status?: string;
  budget?: Money;
  aiBudget?: Money;
  deadline?: string;
  teamIds?: string[];
  milestones?: { label: string; done: boolean }[];
  allowUnpricedAi?: boolean;
}

export async function apiPatchProject(
  businessId: string,
  projectId: string,
  patch: ProjectPatchInput,
  opts?: RequestOpts,
) {
  return api<ApiProject>(
    `/api/v1/businesses/${id(businessId)}/projects/${id(projectId)}`,
    { method: "PATCH", body: patch, signal: opts?.signal },
  );
}

export interface TaskPatchInput {
  title?: string;
  summary?: string;
  etaMinutes?: number;
  ownerAgentId?: string;
  gateAt?: number;
}

export async function apiPatchTask(
  businessId: string,
  projectId: string,
  taskId: string,
  patch: TaskPatchInput,
  opts?: RequestOpts,
) {
  return api<ApiTask>(
    `/api/v1/businesses/${id(businessId)}/projects/${id(projectId)}/tasks/${id(taskId)}`,
    { method: "PATCH", body: patch, signal: opts?.signal },
  );
}
