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
  apiApproveApproval,
  apiGetAgent,
  apiGetApproval,
  apiGetBusiness,
  apiGetProject,
  apiGetTask,
  apiListActivity,
  apiListAgents,
  apiListApprovals,
  apiListBrain,
  apiListProjects,
  apiListTasks,
  apiRejectApproval,
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

const CASES: ClientCase[] = [
  {
    name: "apiGetBusiness",
    call: () => apiGetBusiness(DUMMY),
    template: "/api/v1/businesses/{business_id}",
    schema: "BusinessResponse",
    fields: [
      "id", "name", "tagline", "industry", "location", "ownerName",
      "healthScore", "healthBreakdown", "metrics", "revenueSeries", "contract",
    ],
    money: [],
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
      "aiBudget", "aiSpent", "deadline", "teamIds", "milestones", "contract",
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
