/**
 * Wire-contract conformance: typed client vs backend-generated manifest.
 *
 * Source of truth chain:
 *   backend Pydantic schemas + routes
 *     -> docs/contracts/wire-manifest.json (scripts/gen_wire_manifest.py)
 *     -> pinned fresh by backend/tests/test_wire_manifest.py
 *     -> checked here against the real client functions in lib/api.ts.
 *
 * Method: each client function is invoked against a mocked fetch with dummy
 * ids; the captured method + URL + query are matched to the manifest (no
 * URL copies — the client's own construction is what gets verified).
 *
 * Known limitation: TypeScript cannot reflect interface keys at runtime, so
 * the per-schema field tables below mirror the interfaces in lib/api.ts by
 * discipline (keep them in sync on edit; code review is the backstop).
 * Backend-side drift is caught automatically: any Pydantic/route change
 * without a regenerated manifest fails the backend freshness test first.
 */
import { describe, expect, it, vi } from "vitest";
import {
  apiActivateAgent,
  apiAnswerBrief,
  apiApproveApproval,
  apiConnectIntegration,
  apiCommandProjectRun,
  apiCreateBrain,
  apiCreateBrief,
  apiDisableAgent,
  apiDisconnectIntegration,
  apiGetAgent,
  apiGetApproval,
  apiGetBusiness,
  apiGetIntegrationConnection,
  apiGetListing,
  apiGetProject,
  apiGetProjectRun,
  apiGetTask,
  apiHireAgent,
  apiListIntegrationConnections,
  apiListIntegrationProviders,
  apiListActivity,
  apiListAgents,
  apiListApprovals,
  apiListBrain,
  apiListMarketplace,
  apiListUsage,
  apiListProjects,
  apiListTasks,
  apiPatchProject,
  apiPatchTask,
  apiPlanBrief,
  apiRejectApproval,
  type ProjectRunCommand,
} from "@/lib/api";
import manifest from "../../docs/contracts/wire-manifest.json";

interface ManifestEndpoint {
  method: string;
  path: string;
  response: string;
}

interface ManifestSchema {
  properties: Record<string, { type: string; nullable?: boolean }>;
  required: string[];
}

const wire = manifest as unknown as {
  contract: string;
  endpoints: ManifestEndpoint[];
  schemas: Record<string, ManifestSchema>;
};

interface ClientCase {
  name: string;
  call: () => Promise<unknown>;
  method?: string;
  template: string;
  schema: string;
  fields: string[];
  money: string[];
  query?: Record<string, string>;
}

const DUMMY = "x";
const RUN_PATH = "/api/v1/businesses/{business_id}/projects/{project_id}/run";
const RUN_FIELDS = [
  "runId", "state", "version", "schedulingMode", "reasonCode", "blockerId", "activeTaskId",
  "activeTaskGeneration", "recoveredTaskGeneration", "retryTaskId", "allowedActions",
  "updatedAt", "contract",
];
const RUN_COMMANDS: ProjectRunCommand[] = [
  { action: "start" }, { action: "pause" }, { action: "resume", assumeResponsibility: true },
  { action: "cancel" }, { action: "retry", taskId: DUMMY, expectedTaskGeneration: 2 },
];

const CASES: ClientCase[] = [
  {
    name: "apiGetProjectRun",
    call: () => apiGetProjectRun(DUMMY, DUMMY),
    template: RUN_PATH,
    schema: "ProjectRunResponse",
    fields: RUN_FIELDS,
    money: [],
  },
  ...RUN_COMMANDS.map((command): ClientCase => ({
    name: `apiCommandProjectRun ${command.action}`,
    call: () => apiCommandProjectRun(DUMMY, DUMMY, 4, command),
    method: "POST",
    template: `${RUN_PATH}/${command.action}`,
    schema: "ProjectRunResponse",
    fields: RUN_FIELDS,
    money: [],
  })),
  {
    name: "apiGetBusiness",
    call: () => apiGetBusiness(DUMMY),
    template: "/api/v1/businesses/{business_id}",
    schema: "BusinessResponse",
    fields: [
      "id", "name", "tagline", "industry", "location", "ownerName",
      "healthScore", "healthBreakdown", "metrics", "revenueSeries",
      "aiBudget", "allowUnpricedAi", "contract",
    ],
    money: ["aiBudget"],
  },
  {
    name: "apiListProjects",
    call: () => apiListProjects(DUMMY, { limit: 10, offset: 5 }),
    template: "/api/v1/businesses/{business_id}/projects",
    schema: "ProjectListResponse",
    fields: ["items", "total", "limit", "offset", "contract"],
    money: [],
    query: { limit: "10", offset: "5" },
  },
  {
    name: "apiGetProject",
    call: () => apiGetProject(DUMMY, DUMMY),
    template: "/api/v1/businesses/{business_id}/projects/{project_id}",
    schema: "ProjectResponse",
    fields: [
      "id", "businessId", "title", "goal", "status", "progress", "budget",
      "aiBudget", "aiSpent", "deadline", "teamIds", "milestones",
      "allowUnpricedAi", "contract",
    ],
    money: ["budget", "aiBudget", "aiSpent"],
  },
  {
    name: "apiListTasks",
    call: () => apiListTasks(DUMMY, DUMMY, { limit: 7 }),
    template: "/api/v1/businesses/{business_id}/projects/{project_id}/tasks",
    schema: "TaskListResponse",
    fields: ["items", "total", "limit", "offset", "contract"],
    money: [],
    query: { limit: "7" },
  },
  {
    name: "apiGetTask",
    call: () => apiGetTask(DUMMY, DUMMY, DUMMY),
    template: "/api/v1/businesses/{business_id}/projects/{project_id}/tasks/{task_id}",
    schema: "TaskResponse",
    fields: [
      "id", "businessId", "projectId", "title", "ownerAgentId", "status",
      "progress", "costAccrued", "etaMinutes", "summary", "attempts",
      "approvalGateId", "gateAt", "costRate", "contract",
    ],
    money: ["costAccrued", "costRate"],
  },
  {
    name: "apiListAgents",
    call: () => apiListAgents(DUMMY),
    template: "/api/v1/businesses/{business_id}/agents",
    schema: "AgentListResponse",
    fields: ["items", "total", "limit", "offset", "contract"],
    money: [],
  },
  {
    name: "apiGetAgent",
    call: () => apiGetAgent(DUMMY, DUMMY),
    template: "/api/v1/businesses/{business_id}/agents/{agent_id}",
    schema: "AgentResponse",
    fields: [
      "id", "businessId", "name", "role", "responsibilities", "tools",
      "permissions", "status", "currentTask", "costPerMonth", "performance",
      "hired", "avatarHue", "contract",
    ],
    money: [],
  },
  {
    name: "apiListApprovals",
    call: () => apiListApprovals(DUMMY),
    template: "/api/v1/businesses/{business_id}/approvals",
    schema: "ApprovalListResponse",
    fields: ["items", "total", "limit", "offset", "contract"],
    money: [],
  },
  {
    name: "apiGetApproval",
    call: () => apiGetApproval(DUMMY, DUMMY),
    template: "/api/v1/businesses/{business_id}/approvals/{approval_id}",
    schema: "ApprovalResponse",
    fields: [
      "id", "businessId", "projectId", "title", "requestedBy",
      "requestedByAgentId", "ageLabel", "status", "what", "expectedResult",
      "cost", "affects", "rationale", "gatesTaskId", "contract",
    ],
    money: [],
  },
  {
    name: "apiListActivity",
    call: () => apiListActivity(DUMMY, { projectId: "p", limit: 3 }),
    template: "/api/v1/businesses/{business_id}/activity",
    schema: "ActivityListResponse",
    fields: ["items", "total", "limit", "offset", "contract"],
    money: [],
    query: { project_id: "p", limit: "3" },
  },
  {
    name: "apiListBrain",
    call: () => apiListBrain(DUMMY, { category: "goals", offset: 2 }),
    template: "/api/v1/businesses/{business_id}/brain",
    schema: "BrainListResponse",
    fields: ["items", "total", "limit", "offset", "contract"],
    money: [],
    query: { category: "goals", offset: "2" },
  },
  {
    name: "apiListUsage",
    call: () => apiListUsage(DUMMY, { projectId: "p", taskId: "t", limit: 4 }),
    template: "/api/v1/businesses/{business_id}/usage",
    schema: "UsageListResponse",
    fields: ["items", "total", "limit", "offset", "summary", "contract"],
    money: [],
    query: { project_id: "p", task_id: "t", limit: "4" },
  },
  {
    name: "apiApproveApproval",
    call: () => apiApproveApproval(DUMMY, DUMMY),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/approvals/{approval_id}/approve",
    schema: "DecisionResponse",
    fields: ["outcome", "approvalId", "status", "contract"],
    money: [],
  },
  {
    name: "apiRejectApproval",
    call: () => apiRejectApproval(DUMMY, DUMMY),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/approvals/{approval_id}/reject",
    schema: "DecisionResponse",
    fields: ["outcome", "approvalId", "status", "contract"],
    money: [],
  },
  {
    name: "apiCreateBrain",
    call: () => apiCreateBrain(DUMMY, { category: "goals", title: "t", body: "b" }),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/brain",
    schema: "BrainResponse",
    fields: ["id", "businessId", "category", "title", "body", "updatedLabel", "contract"],
    money: [],
  },
  {
    name: "apiPatchProject",
    call: () => apiPatchProject(DUMMY, DUMMY, { title: "t", status: "review" }),
    method: "PATCH",
    template: "/api/v1/businesses/{business_id}/projects/{project_id}",
    schema: "ProjectResponse",
    fields: [
      "id", "businessId", "title", "goal", "status", "progress", "budget",
      "aiBudget", "aiSpent", "deadline", "teamIds", "milestones",
      "allowUnpricedAi", "contract",
    ],
    money: ["budget", "aiBudget", "aiSpent"],
  },
  {
    name: "apiPatchTask",
    call: () => apiPatchTask(DUMMY, DUMMY, DUMMY, { title: "t", etaMinutes: 5 }),
    method: "PATCH",
    template: "/api/v1/businesses/{business_id}/projects/{project_id}/tasks/{task_id}",
    schema: "TaskResponse",
    fields: [
      "id", "businessId", "projectId", "title", "ownerAgentId", "status",
      "progress", "costAccrued", "etaMinutes", "summary", "attempts",
      "approvalGateId", "gateAt", "costRate", "contract",
    ],
    money: ["costAccrued", "costRate"],
  },
  {
    name: "apiListMarketplace",
    call: () => apiListMarketplace(DUMMY, { limit: 10, offset: 5 }),
    method: "GET",
    template: "/api/v1/businesses/{business_id}/marketplace",
    schema: "MarketplaceListResponse",
    fields: ["items", "total", "limit", "offset", "contract"],
    money: [],
    query: { limit: "10", offset: "5" },
  },
  {
    name: "apiGetListing",
    call: () => apiGetListing(DUMMY, DUMMY),
    method: "GET",
    template: "/api/v1/businesses/{business_id}/marketplace/{slug}",
    schema: "MarketplaceResponse",
    fields: [
      "slug", "name", "role", "blurb", "price", "priceNote", "rating",
      "installs", "badge", "capabilities", "permissions", "category",
      "hired", "contract",
    ],
    money: [],
  },
  {
    name: "apiHireAgent",
    call: () => apiHireAgent(DUMMY, DUMMY),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/marketplace/{slug}/hire",
    schema: "AgentResponse",
    fields: [
      "id", "businessId", "name", "role", "responsibilities", "tools",
      "permissions", "status", "currentTask", "costPerMonth", "performance",
      "hired", "avatarHue", "contract",
    ],
    money: [],
  },
  {
    name: "apiActivateAgent",
    call: () => apiActivateAgent(DUMMY, DUMMY),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/agents/{agent_id}/activate",
    schema: "AgentResponse",
    fields: [
      "id", "businessId", "name", "role", "responsibilities", "tools",
      "permissions", "status", "currentTask", "costPerMonth", "performance",
      "hired", "avatarHue", "contract",
    ],
    money: [],
  },
  {
    name: "apiDisableAgent",
    call: () => apiDisableAgent(DUMMY, DUMMY),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/agents/{agent_id}/disable",
    schema: "AgentResponse",
    fields: [
      "id", "businessId", "name", "role", "responsibilities", "tools",
      "permissions", "status", "currentTask", "costPerMonth", "performance",
      "hired", "avatarHue", "contract",
    ],
    money: [],
  },
  {
    name: "apiCreateBrief",
    call: () => apiCreateBrief(DUMMY, "Launch it."),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/briefs",
    schema: "BriefResponse",
    fields: [
      "id", "businessId", "goal", "status", "questions", "answers",
      "summary", "projectId", "finalReport", "contract",
    ],
    money: [],
  },
  {
    name: "apiAnswerBrief",
    call: () => apiAnswerBrief(DUMMY, DUMMY, [{ question_id: "q1", text: "A1" }]),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/briefs/{brief_id}/answers",
    schema: "BriefResponse",
    fields: [
      "id", "businessId", "goal", "status", "questions", "answers",
      "summary", "projectId", "finalReport", "contract",
    ],
    money: [],
  },
  {
    name: "apiPlanBrief",
    call: () => apiPlanBrief(DUMMY, DUMMY),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/briefs/{brief_id}/plan",
    schema: "PlanResponse",
    fields: ["briefId", "project", "tasks", "approvals", "contract"],
    money: [],
  },
  {
    name: "apiListIntegrationProviders",
    call: () => apiListIntegrationProviders(DUMMY),
    template: "/api/v1/businesses/{business_id}/integrations/providers",
    schema: "IntegrationProviderListResponse",
    fields: ["providers", "contract"],
    money: [],
  },
  {
    name: "apiListIntegrationConnections",
    call: () => apiListIntegrationConnections(DUMMY),
    template: "/api/v1/businesses/{business_id}/integrations",
    schema: "IntegrationConnectionListResponse",
    fields: ["connections", "contract"],
    money: [],
  },
  {
    name: "apiGetIntegrationConnection",
    call: () => apiGetIntegrationConnection(DUMMY, DUMMY),
    template: "/api/v1/businesses/{business_id}/integrations/{connection_id}",
    schema: "IntegrationConnectionResponse",
    fields: [
      "id", "provider", "providerName", "externalAccountId", "displayName",
      "status", "health", "scopes", "capabilities", "expiresAt",
      "lastRefreshedAt", "lastError", "canWrite", "contract",
    ],
    money: [],
  },
  {
    name: "apiConnectIntegration",
    call: () => apiConnectIntegration(DUMMY, DUMMY, true),
    method: "POST",
    template: "/api/v1/businesses/{business_id}/integrations/{provider}/connect",
    schema: "IntegrationConnectResponse",
    fields: ["authorizationUrl", "provider", "scopes", "expiresAt", "contract"],
    money: [],
  },
  {
    name: "apiDisconnectIntegration",
    call: () => apiDisconnectIntegration(DUMMY, DUMMY),
    method: "DELETE",
    template: "/api/v1/businesses/{business_id}/integrations/{connection_id}",
    schema: "IntegrationDisconnectResponse",
    fields: ["outcome", "connectionId", "contract"],
    money: [],
  },
];

// Item shapes are covered through their list/single endpoints above; this
// table pins the remaining single-item schemas to their field sets.
const ITEM_SCHEMAS: Record<string, { fields: string[]; money: string[] }> = {
  ActivityResponse: {
    fields: ["id", "at", "agentName", "text", "kind", "cost", "contract"],
    money: ["cost"],
  },
  BrainResponse: {
    fields: ["id", "businessId", "category", "title", "body", "updatedLabel", "contract"],
    money: [],
  },
  UsageItemResponse: {
    fields: [
      "id", "businessId", "projectId", "taskId", "agentId", "attempt",
      "provider", "model", "callKind", "providerRequestId", "promptTokens",
      "completionTokens", "totalTokens", "cachedTokens", "reasoningTokens",
      "costAmount", "currency", "costSource", "usageStatus", "tariffVersion",
      "occurredAt",
    ],
    money: ["costAmount"],
  },
  UsageCallKindSummary: {
    fields: ["confirmedCost", "estimatedCost", "unknownCount"],
    money: ["confirmedCost", "estimatedCost"],
  },
  UsageReservationSummary: {
    fields: ["active", "ambiguous", "consumed", "released", "writtenOff", "reconciled"],
    money: ["active", "ambiguous", "consumed", "released", "writtenOff", "reconciled"],
  },
  UsageSummary: {
    fields: ["confirmedCost", "estimatedCost", "unknownCount", "byCallKind", "reservations"],
    money: ["confirmedCost", "estimatedCost"],
  },
  IntegrationCapabilityResponse: {
    fields: ["id", "title", "risk"],
    money: [],
  },
  IntegrationProviderResponse: {
    fields: [
      "id", "name", "description", "docsUrl", "readScopes", "writeScopes",
      "capabilities", "configured", "missingConfiguration", "contract",
    ],
    money: [],
  },
  IntegrationConnectionCapabilityResponse: {
    fields: ["id", "title", "risk", "granted"],
    money: [],
  },
  IntegrationConnectionResponse: {
    fields: [
      "id", "provider", "providerName", "externalAccountId", "displayName",
      "status", "health", "scopes", "capabilities", "expiresAt",
      "lastRefreshedAt", "lastError", "canWrite", "contract",
    ],
    money: [],
  },
};

function templateToRegExp(template: string): RegExp {
  const pattern = template.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\\\{[^}]+\\\}/g, "[^/]+");
  return new RegExp(`^${pattern}$`);
}

describe("wire manifest conformance", () => {
  it("manifest targets the frozen wire contract", () => {
    expect(wire.contract).toBe("v1");
    expect(wire.endpoints.length).toBeGreaterThan(0);
    expect(Object.keys(wire.schemas).length).toBeGreaterThan(0);
  });

  for (const c of CASES) {
    it(`${c.name}: method, path template, query, and schema fields`, async () => {
      const calls: { url: string; init?: RequestInit }[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string, init?: RequestInit) => {
          calls.push({ url, init });
          return new Response(JSON.stringify({ items: [], total: 0, limit: 0, offset: 0 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }),
      );
      try {
        await c.call();
      } finally {
        vi.unstubAllGlobals();
      }
      expect(calls).toHaveLength(1);
      const wantMethod = c.method ?? "GET";
      expect(calls[0].init?.method ?? "GET").toBe(wantMethod);

      const url = new URL(calls[0].url);
      const entry = wire.endpoints.find((e) => e.method === wantMethod && e.path === c.template);
      expect(entry, `manifest lacks ${c.template}`).toBeDefined();
      expect(url.pathname).toMatch(templateToRegExp(c.template));
      expect(entry?.response).toBe(c.schema);
      if (c.query) {
        for (const [key, value] of Object.entries(c.query)) {
          expect(url.searchParams.get(key)).toBe(value);
        }
      }

      const schema = wire.schemas[c.schema];
      expect(schema, `manifest lacks schema ${c.schema}`).toBeDefined();
      for (const field of c.fields) {
        expect(schema.properties[field], `${c.schema} lacks ${field}`).toBeDefined();
      }
      for (const required of schema.required) {
        expect(c.fields).toContain(required);
      }
      const decimalProps = Object.entries(schema.properties)
        .filter(([, spec]) => spec.type === "decimal-as-string")
        .map(([name]) => name)
        .sort();
      expect(decimalProps).toEqual([...c.money].sort());
    });
  }

  for (const [schemaName, table] of Object.entries(ITEM_SCHEMAS)) {
    it(`${schemaName}: item fields and money markers`, () => {
      const schema = wire.schemas[schemaName];
      expect(schema).toBeDefined();
      for (const field of table.fields) {
        expect(schema.properties[field], `${schemaName} lacks ${field}`).toBeDefined();
      }
      for (const required of schema.required) {
        expect(table.fields).toContain(required);
      }
      const decimalProps = Object.entries(schema.properties)
        .filter(([, spec]) => spec.type === "decimal-as-string")
        .map(([name]) => name)
        .sort();
      expect(decimalProps).toEqual([...table.money].sort());
    });
  }
});
