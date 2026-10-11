/**
 * Backend API client (Stage 6 frontend slice).
 *
 * - Base URL from `NEXT_PUBLIC_API_URL` (default: local backend).
 * - Session cookie is HttpOnly: the browser sends it automatically, so we
 *   only set `credentials: "include"`. No session id, password, or CSRF
 *   secret ever touches localStorage — CSRF lives in memory only.
 * - Mutations carry the in-memory `X-CSRF-Token`; 401s notify the session
 *   store through `onUnauthorized` (wired by the auth store, no imports).
 */

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
  const raw = process.env.NEXT_PUBLIC_API_URL ?? "";
  return raw.replace(/\/$/, "") || "http://127.0.0.1:8000";
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
