/**
 * Deterministic OpenAI-compatible LLM stub for LIVE E2E (Stage 11).
 *
 * Serves the production `openai_compat` adapter over plain HTTP — no vendor
 * SDK, no keys, no network beyond localhost. The backend points at it via
 * APP_LLM_PROVIDER=openai_compat + APP_LLM_BASE_URL (test-only config; the
 * default mock runtime is untouched).
 *
 * Routing is by system-prompt marker (the only stable kind tag in the
 * request). Dynamic endpoints parse the embedded facts/roster from the user
 * prompt and echo them back VALIDLY, so server-side truthfulness validation
 * runs against real rows instead of being bypassed:
 * - intake questions → 2 stable questions (q1/q2);
 * - brief summary → static 6-key envelope;
 * - plan → 2 tasks owned by the first roster agent (t2 gated, depends on t1);
 * - task execution → valid result envelope (tasks COMPLETE);
 * - finalize → report built from the embedded persisted facts (exact goal,
 *   real task ids/titles/statuses) + one grounded learning.
 *
 * Negative modes (deterministic, marker-driven, no extra endpoints):
 * - user prompt contains "[e2e-invalid]" → 200 with non-JSON garbage
 *   (validation budget exhausts → 422, nothing persisted);
 * - user prompt contains "[e2e-down]" → HTTP 500 (provider-error path).
 *
 * Usage: `node e2e/llm-stub.mjs --port 8099` (Playwright webServer manages
 * it; /health gates startup).
 */
import http from "node:http";

const PORT = Number(process.argv[process.argv.indexOf("--port") + 1] || "8099");

function questions() {
  return {
    questions: [
      { id: "q1", text: "Who is the offer for?", options: ["Newcomers", "Regulars"] },
      { id: "q2", text: "What does success look like in numbers?" },
    ],
  };
}

function summary() {
  return {
    goal: "E2E intake goal",
    context: ["E2E context"],
    desired_outcome: "E2E outcome",
    success_criteria: ["E2E criterion"],
    assumptions: ["E2E assumption"],
    open_questions: [],
  };
}

function planFromRoster(rosterIds) {
  const owner = rosterIds[0] ?? "ag_unknown";
  return {
    project: { title: "E2E Intake Project", goal: "E2E intake goal", budget: 100, ai_budget: 10 },
    tasks: [
      { key: "t1", title: "Prepare intake groundwork", owner_agent_id: owner, summary: "First step." },
      {
        key: "t2",
        title: "Ship intake slice",
        owner_agent_id: owner,
        summary: "Second step.",
        depends_on: ["t1"],
        gate: {
          title: "Approve intake slice",
          requested_by_agent_id: owner,
          requested_by: "E2E",
          what: "Ship it.",
          expected_result: "It ships.",
        },
      },
    ],
  };
}

function taskResult() {
  return { summary: "E2E stubbed task result.", findings: ["E2E finding one."] };
}

function parseFacts(userContent) {
  const start = userContent.indexOf("{");
  if (start < 0) return null;
  // Facts are the first top-level JSON object in the prompt.
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = start; i < userContent.length; i++) {
    const ch = userContent[i];
    if (inString) {
      if (escape) escape = false;
      else if (ch === "\\") escape = true;
      else if (ch === '"') inString = false;
    } else if (ch === '"') {
      inString = true;
    } else if (ch === "{") {
      depth++;
    } else if (ch === "}") {
      depth--;
      if (depth === 0) {
        try {
          return JSON.parse(userContent.slice(start, i + 1));
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}

function finalizeFromFacts(facts) {
  const completed = (facts.tasks ?? []).filter((t) => t.status === "completed");
  const failed = (facts.tasks ?? []).filter((t) => t.status === "failed");
  return {
    report: {
      goal: facts.goal ?? "",
      completed_tasks: completed.map((t) => ({
        id: t.id,
        title: t.title,
        summary: (t.summary || "Done.").slice(0, 200),
      })),
      failed_tasks: failed.map((t) => ({
        id: t.id,
        title: t.title,
        reason: "E2E recorded failure.",
      })),
      limitations: ["E2E stubbed run."],
      recommendations: ["Ship it."],
    },
    learnings: [
      {
        category: "results",
        title: "E2E intake learning",
        body: "Small verified slices ship faster than big unverified ones.",
      },
    ],
  };
}

function route(system, user) {
  // Prompts wrap lines: normalize whitespace before marker matching.
  const flat = system.replace(/\s+/g, " ");
  if (flat.includes("running intake")) return questions();
  if (flat.includes("writing a structured brief")) return summary();
  if (flat.includes("turning an approved brief")) {
    const rosterIds = [...user.matchAll(/^- (\S+) —/gm)].map((m) => m[1]);
    return planFromRoster(rosterIds);
  }
  if (flat.includes("writing the final report")) {
    const facts = parseFacts(user);
    if (!facts) return null;
    return finalizeFromFacts(facts);
  }
  if (user.includes("Produce the task result as JSON")) return taskResult();
  return null;
}

function completion(content) {
  return {
    id: "e2e-chatcmpl-1",
    object: "chat.completion",
    model: "e2e-stub",
    choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
    usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
  };
}

const server = http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok" }));
    return;
  }
  if (req.method !== "POST" || !req.url.endsWith("/chat/completions")) {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
    return;
  }
  let raw = "";
  req.on("data", (chunk) => {
    raw += chunk;
  });
  req.on("end", () => {
    let body;
    try {
      body = JSON.parse(raw);
    } catch {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "bad request" }));
      return;
    }
    if (raw.includes("[e2e-down]")) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "e2e stub outage" }));
      return;
    }
    const messages = body.messages ?? [];
    const system = messages.find((m) => m.role === "system")?.content ?? "";
    const user = messages.filter((m) => m.role === "user").map((m) => m.content).join("\n") ?? "";
    const answer = raw.includes("[e2e-invalid]") ? null : route(system, user);
    const content = answer === null ? "definitely not json {{{" : JSON.stringify(answer);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(completion(content)));
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`e2e llm stub on http://127.0.0.1:${PORT}`);
});
