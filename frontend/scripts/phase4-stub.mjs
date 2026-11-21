/**
 * TEMPORARY Phase 4 measurement harness (deleted after verification).
 *
 * Serves the exact LIVE read contract the dashboard polls, logs every request
 * with a timestamp, and exposes control endpoints so a test can flip a task's
 * status without touching the app.
 *
 *   GET  /__stats        -> {total, byPath, log}
 *   POST /__task         -> body {"status":"working"|"completed"|...}
 *   POST /__down         -> close every socket (simulated outage begins)
 *   POST /__up           -> stop refusing new connections
 */
import http from "node:http";

const PORT = 8788;
const ORIGIN = "http://127.0.0.1:3014";
const BIZ = "biz_live";
const PROJECT = "proj_1";

let taskStatus = "not_started";
let refusing = false;
let log = [];

function send(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "access-control-allow-origin": ORIGIN,
    "access-control-allow-credentials": "true",
    "cache-control": "no-store",
  });
  res.end(text);
}

function page(items) {
  return { items, total: items.length, limit: 200, offset: 0, contract: "v1" };
}

const business = {
  id: BIZ,
  name: "Northstar Live",
  tagline: "Server-backed workspace",
  industry: "SaaS",
  location: "Berlin",
  ownerName: "Ada Operator",
  healthScore: 78,
  healthBreakdown: [{ label: "Delivery", score: 80 }],
  metrics: {
    revenue: 52000,
    revenueDelta: 4.2,
    customers: 812,
    customersDelta: 1.1,
    conversion: 3.4,
    conversionDelta: -0.2,
    leads: 120,
  },
  revenueSeries: [
    { label: "Jan", value: 40000 },
    { label: "Feb", value: 52000 },
  ],
  aiBudget: "100.00",
  contract: "v1",
};

const project = {
  id: PROJECT,
  businessId: BIZ,
  title: "Ship onboarding",
  goal: "A predictable onboarding flow",
  status: "in_progress",
  progress: taskStatus === "completed" ? 100 : 35,
  budget: "1000.00",
  aiBudget: "50.00",
  aiSpent: "2.20",
  deadline: "30 May 2026",
  teamIds: ["ag_1"],
  milestones: [],
  contract: "v1",
};

function agents() {
  return [
    {
      id: "ag_1",
      businessId: BIZ,
      name: "Atlas",
      role: "Team Lead",
      responsibilities: [],
      tools: [],
      permissions: {},
      status: taskStatus === "working" ? "working" : "available",
      currentTask: taskStatus === "working" ? "Ship onboarding" : null,
      costPerMonth: 19,
      performance: {},
      hired: true,
      avatarHue: 217,
      contract: "v1",
    },
  ];
}

const approvals = [
  {
    id: "ap_1",
    businessId: BIZ,
    projectId: PROJECT,
    title: "Publish landing copy",
    requestedBy: "Atlas",
    requestedByAgentId: "ag_1",
    ageLabel: "2h",
    status: "pending",
    what: "Publish",
    expectedResult: "Live page",
    cost: "0.00",
    affects: "Marketing site",
    rationale: "Needs a human",
    gatesTaskId: null,
    contract: "v1",
  },
];

const activity = [
  {
    id: "ev_1",
    at: new Date().toISOString(),
    agentName: "Atlas",
    text: taskStatus === "completed" ? "Finished onboarding" : "Planning onboarding",
    kind: "work",
    cost: null,
    contract: "v1",
  },
];

const usage = {
  items: [],
  total: 0,
  limit: 200,
  offset: 0,
  summary: {
    confirmedCost: "0.00",
    estimatedCost: "0.00",
    unknownCount: 0,
    byCallKind: {},
    reservations: { active: 0, ambiguous: 0, consumed: 0, released: 0, writtenOff: 0 },
  },
  contract: "v1",
};

function tasks() {
  return page([
    {
      id: "task_1",
      businessId: BIZ,
      projectId: PROJECT,
      title: "Draft onboarding copy",
      ownerAgentId: "ag_1",
      status: taskStatus,
      progress: taskStatus === "completed" ? 100 : taskStatus === "working" ? 55 : 0,
      costAccrued: "1.10",
      etaMinutes: 8,
      summary: "First pass",
      attempts: 0,
      approvalGateId: null,
      gateAt: null,
      costRate: null,
      contract: "v1",
    },
  ]);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const path = url.pathname;

  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "access-control-allow-origin": ORIGIN,
      "access-control-allow-credentials": "true",
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": req.headers["access-control-request-headers"] ?? "*",
    });
    res.end();
    return;
  }

  if (path === "/__stats") return send(res, 200, { total: log.length, byPath: group(log), log });
  if (path === "/__task" && req.method === "POST") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      taskStatus = JSON.parse(body || "{}").status ?? "not_started";
      send(res, 200, { ok: true, taskStatus });
    });
    return;
  }
  if (path === "/__down" && req.method === "POST") {
    refusing = true;
    server.closeAllConnections?.();
    return send(res, 200, { ok: true, refusing });
  }
  if (path === "/__up" && req.method === "POST") {
    refusing = false;
    return send(res, 200, { ok: true, refusing });
  }
  if (refusing) {
    // Simulated outage: drop the socket so the client sees a network failure.
    req.socket.destroy();
    return;
  }

  log.push({ at: Date.now(), method: req.method, path: url.pathname + url.search });

  if (path === "/api/v1/auth/me") {
    return send(res, 200, {
      user: { id: "user_live", email: "ada@example.com", displayName: "Ada Operator" },
      csrf_token: "csrf-test",
      businesses: [BIZ],
    });
  }
  if (path === `/api/v1/businesses/${BIZ}`) return send(res, 200, business);
  if (path === `/api/v1/businesses/${BIZ}/projects`) return send(res, 200, page([project]));
  if (path === `/api/v1/businesses/${BIZ}/projects/${PROJECT}/tasks`) return send(res, 200, tasks());
  if (path === `/api/v1/businesses/${BIZ}/agents`) return send(res, 200, page(agents()));
  if (path === `/api/v1/businesses/${BIZ}/approvals`) return send(res, 200, page(approvals));
  if (path === `/api/v1/businesses/${BIZ}/activity`) return send(res, 200, page(activity));
  if (path === `/api/v1/businesses/${BIZ}/usage`) return send(res, 200, usage);
  return send(res, 404, { error: { code: "not_found", message: `No stub for ${path}` } });
});

function group(entries) {
  const out = {};
  for (const entry of entries) {
    const key = entry.path.split("?")[0];
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}

server.listen(PORT, "127.0.0.1", () => console.log(`stub on ${PORT}`));
