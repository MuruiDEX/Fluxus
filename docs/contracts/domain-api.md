# Domain ↔ API contract (TypeScript ↔ Pydantic) — frozen at Stage 0

Source of truth for shapes: `frontend/lib/domain/types.ts`.
Source of truth for wire rules: this document.
Backend Pydantic models MUST mirror this document field-for-field.
Any deviation requires a contract amendment (new section below + minor version bump).

## 1. Entities and required fields

| Entity | TS type | Key fields (all required unless marked `?`) |
|---|---|---|
| Business | `Business` | `id, name, tagline, industry, location, ownerName, healthScore, healthBreakdown[{label, score}], metrics{revenue, revenueDelta, customers, customersDelta, conversion, conversionDelta, leads}, revenueSeries[{label, value}], aiBudget?, allowUnpricedAi?` |
| Agent | `Agent` | `id, businessId, name, role, responsibilities[], tools[], permissions{read[], write[], actions[], denied[]}, status: AgentStatus, currentTask?, costPerMonth, performance{completionRate, avgMinutes, approvalRate, rating}, hired, avatarHue` |
| Task | `Task` | `id, businessId, projectId, title, ownerAgentId, status: TaskStatus, progress, dependsOn: ID[], costAccrued, etaMinutes, summary, attempts?, approvalGateId?, gateAt?, costRate?` |
| Project | `Project` | `id, businessId, title, goal, status: ProjectStatus, progress, budget, aiBudget, aiSpent, deadline, teamIds: ID[], milestones[{label, done}], allowUnpricedAi?` |
| Approval | `Approval` | `id, businessId, projectId, title, requestedBy, requestedByAgentId, ageLabel, status: ApprovalStatus, what, expectedResult, cost, affects, rationale, gatesTaskId?` |
| BrainEntry | `BrainEntry` | `id, businessId, category: BrainCategory, title, body, updatedLabel` |
| MarketplaceAgent | `MarketplaceAgent` | `slug, name, role, blurb, price, priceNote, rating, installs, badge?, capabilities[], permissions, category, installed?, installedVersion?` |
| Pack | `Pack` | `slug, version, name, description, blurb, price, priceNote, rating, installs, installed, installedVersion?, installedAt?, agents[], workflow?` |
| ActivityEvent | `ActivityEvent` | `id, at, agentName, text, kind: ActivityKind, cost?` |
| Brief | `Brief` | `id, businessId, goal, status: BriefStatus, questions[{id, text, options?}], answers?, summary?, projectId?` |

## 2. Enumerations (exact wire literals — case-sensitive)

```text
AgentStatus  = available | thinking | working | waiting_dependency
             | waiting_approval | reviewing | completed | failed | escalated
TaskStatus   = not_started | working | waiting_dependency | waiting_assets
             | waiting_approval | completed | failed
ProjectStatus = planning | in_progress | review | completed
ApprovalStatus = pending | approved | rejected
BriefStatus = draft | questions_ready | brief_ready | planned | running
            | completed | failed
BrainCategory  = company | audience | model | brand | goals
               | decisions | knowledge | results
MarketplaceCategory = employee | pack | automation | template | integration
ActivityKind = work | approval | system | result
AutopilotLevel = manual | assist | semi | full
```

Rules:

- Unknown enum literal on input → `422` with `code: "invalid_enum"` (never coerce, never default).
- New literals may only be ADDED (minor version); existing literals are never
  renamed or removed (that is a major version).
- Frontend MUST render unknown future literals with a neutral fallback badge,
  never crash.

## 3. Identifiers

- `ID` = non-empty string, max 128 chars, `^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$`.
- Seed/demo ids (`ag_lead`, `t_market`, `proj_krakow_launch`, `biz_northstar`)
  are data, not a format requirement; server-generated ids SHOULD use the
  same charset (UUID without braces is acceptable).
- `MarketplaceAgent.slug` is kebab-case, immutable once published (renaming a
  slug is a breaking change for hires referencing it).
- Cross-entity references (`ownerAgentId`, `dependsOn[]`, `projectId`,
  `businessId`, `approvalGateId`, `gatesTaskId`, `teamIds[]`) MUST resolve to
  existing rows in the same `businessId` scope; dangling references →
  `422 code: "dangling_reference"`.

## 4. Dates, times, money

- Wire format for ALL timestamps: RFC 3339 / ISO 8601 UTC, e.g.
  `2026-10-09T07:00:00Z`. Naive datetimes are rejected (`422`).
- Current frontend display strings (`"2h ago"`, `"just now"`,
  `"30 May 2026"`) are PRESENTATION ONLY and MUST be derived client-side
  from ISO timestamps. The server never parses or emits them.
- Money: JSON numbers in major currency units with max 2 decimals for ledger
  fields (`costAccrued`, `aiSpent`); currency is implied USD in this phase and
  MUST become explicit (`currency` field) before multi-currency support.

## 5. Numbers and progress

- `progress`: number `0–100` inclusive; server clamps on write, never wraps.
- `costAccrued` / `aiSpent`: `>= 0`, rounded to 2 decimals; the invariant
  `project.aiSpent == Σ scoped tasks.costAccrued + Σ confirmed task-less
  project-scoped usage` is enforced server-side (today: enforced by the
  frontend engine + Vitest reconciliation test).
- `gateAt`: defaults to `100` when absent; `attempts`: defaults to `0`.

### 5.1 Spend semantics (frozen for Stage 2 persistence)

- **Counted records:** every `Task` row whose `(businessId, projectId)` matches
  the project scope, using each row's *current* `costAccrued`, PLUS every
  `confirmed` usage-ledger row with the same `(businessId, projectId)` and
  `task_id IS NULL` (Stage 16.1: project-scoped Team Lead calls that have no
  task). The two terms are disjoint by construction, so each dollar is counted
  exactly once. Business-scoped usage (`project_id IS NULL`) never contributes
  to project spend. Activity events and approvals never carry spend.
- **Derived, never incremented:** `project.aiSpent` is a cached aggregate.
  Writers MUST NOT `aiSpent += delta`. The only legal mutation is full
  recompute (`SUM(costAccrued) + SUM(confirmed task-less ledger cost)` over the
  scope) executed in the SAME transaction as the task-cost writes, with the
  project row locked (`SELECT … FOR UPDATE`), so concurrent writers serialize.
- **Double-count prevention:** recompute is idempotent — same task and ledger
  state always yields the same `aiSpent`. There is exactly one recompute path
  (repository helper); stage-4 orchestration and Team Lead settlement call it,
  never reimplement it.
- **Rounding and currency:** `Decimal`, quantum `0.01`, `ROUND_HALF_UP`
  (matches frontend `Math.round` tick-for-tick). Storage `NUMERIC(12,2)`.
  Single implied currency (USD); no currency column until multi-currency
  support (which then becomes a contract amendment, not silent behavior).

### 5.2 Usage accounting (Stage 16)

- **Append-only ledger:** `usage_records` records one canonical row per actual
  provider attempt, uniquely keyed by `idempotency_key`; there is no
  update/delete path. Corrections arrive as new linked rows, never as edits.
- **Cost truth:** an unknown cost is `null` — never `0`. Provider-reported cost
  (when present) is authoritative and `confirmed`. A locally tariff-computed
  cost is `confirmed` only when the provider actually reported token usage;
  with usage missing it is `estimated`. With no tariff the cost is `null` with
  `usageStatus: "unknown"`.
- **Call coverage (v1.18):** every billable provider call is metered — task
  execution plus all four Team Lead generations: `brief_questions`,
  `brief_summary`, `plan_generation` and `brief_finalize`. Each ledger row
  carries a validated `call_kind` (`task_execution` | `brief_questions` |
  `brief_summary` | `plan_generation` | `brief_finalize`; migration 0008,
  nullable for backwards compatibility with legacy rows). Calls that run
  before a project exists (questions/summary/plan) are business-scoped:
  `project_id` and `task_id` are `NULL`, they are visible in the business
  usage summary, and they never enter project spend.
- **Derived spend:** `task.costAccrued = SUM(confirmed usage cost)` for the
  task; `project.aiSpent` is the §5.1 recompute over task-linked *and*
  confirmed task-less project-scoped usage. `estimated`/`unknown` rows are
  tracked but never presented as settled money.
- **Reservations:** before any provider call on a budgeted project
  (`aiBudget > 0`), a `budget_reservations` row is created BEFORE the provider
  request and outside its transaction (task execution: in the SAME transaction
  as the task claim; Team Lead finalization: its own committed transaction
  before the call). `task_id` is optional for project-scoped Team Lead calls.
  The ceiling counts `aiSpent` + task-less `estimated` exposure + `active`/
  `ambiguous` reservations; there is no transaction held across the network
  call.
- **Business budget (v1.19):** business-scoped calls (questions/summary/plan,
  `project_id IS NULL`) reserve against `Business.aiBudget` when it is `> 0`.
  The ceiling counts ALL business usage (`confirmed` + `estimated`, project-
  linked and business-scoped) plus every `active`/`ambiguous` business hold, so
  the business budget is an umbrella over all AI attempts. Only business-scoped
  calls are refused by it: project-scoped calls keep their existing project
  ceiling and may still run after the business ceiling would refuse a
  business-scoped call. `Business.aiBudget` of `0` means no limit and preserves
  the bare-ledger behavior (no reservation). A budgeted business with no
  provable upper bound is refused `budget_unpriced` unless
  `Business.allowUnpricedAi` opts into a `best_effort` reserve. Additive
  `PATCH /api/v1/businesses/{business_id}` accepts `aiBudget` and
  `allowUnpricedAi` (all other fields 422) and writes one `system` audit event;
  `BusinessResponse` carries both fields additively.
- **Post-send malformed response:** the provider answered, so the attempt may
  already be billed. It is recorded `estimated` from the versioned tariff
  bound when that bound is known, otherwise its hold stays `ambiguous`; it is
  NEVER released as free and NEVER recorded as zero cost.
- **Fail-closed budget:** a provable upper bound requires a known tariff AND a
  capped output (`llm_max_tokens`); otherwise a budgeted project or business is
  refused with `budget_unpriced` (409) and no provider call — unless that scope
  opts in via `allowUnpricedAi: true`, which downgrades to a `best_effort`
  internal reserve.
- **Ambiguity is never auto-released:** a post-send failure of unknown outcome
  yields a permanently held `ambiguous` reservation (never retried). Recovery
  of a stale `working` task converts its `active` hold to `ambiguous` and
  releases `ambiguous` holds only when an operator configures
  `usage_ambiguous_release_minutes > 0` (default `0` = never).
- **Read exposure:** `GET /api/v1/businesses/{business_id}/usage` lists ledger
  rows (optional `?project_id` / `?task_id`, `limit`/`offset`) plus a derived
  `summary` separating confirmed/estimated money, unknown count, reservation
  amounts by status, and a `byCallKind` split of confirmed cost, estimated cost
  and unknown count per `call_kind` (legacy `NULL` kinds are grouped under the
  empty-string key). `UsageItemResponse.callKind` is additive.
- **Finalize snapshot:** `FinalizeResponse.costs` is read from the ledger at
  finalization time and includes this finalization's confirmed usage;
  `ai_estimated` is reported separately and never added to `ai_spent`. The
  `note` documents exactly that. Repeating a completed finalization returns the
  stored result without another provider call.

## 6. Error envelope (all API errors)

```json
{
  "error": {
    "code": "string_code",
    "message": "human-readable, safe to display",
    "details": {},
    "trace_id": "opaque id for log correlation"
  }
}
```

Standard codes: `not_found`, `validation_error`, `invalid_enum`,
`dangling_reference`, `conflict`, `budget_exceeded`, `budget_unpriced`,
`approval_required`, `rate_limited`, `unauthorized`, `forbidden`, `internal`.
HTTP status mapping: 404 / 422 / 422 / 422 / 409 / 402? no — `budget_exceeded`
→ `409`; `budget_unpriced` → `409`; `approval_required` → `409`;
`rate_limited` → `429`;
`unauthorized` → `401`; `forbidden` → `403`; `internal` → `500`.
`message` MUST NOT leak internals (no tracebacks, SQL, paths, secrets).

Provider-side failures use one additional additive code (Stage 3):
`provider_auth_error` — the backend's own provider credentials were rejected.
It MUST NOT be confused with user-facing `unauthorized` (user login/session).
Recorded as amendment v1.2 below.

## 7. Compatibility rules

1. Additive changes only within `/api/v1` (new optional fields, new endpoints,
   new enum literals).
2. Renames, removals, type changes, new required fields → `/api/v2`.
3. Frontend sends `X-Client-Contract: 1` header; server rejects unknown major
   with `409 code: "contract_mismatch"`.
4. Every response that carries domain objects echoes the contract version:
   `"contract": "v1"`.
5. The deterministic engine semantics (`stepScope`: parallel progress,
   gate pause/resume, cost accrual, finalize→Brain) are part of the contract;
   the backend port MUST reproduce them tick-for-tick (verified by porting
   the Vitest scenarios to pytest at Stage 4).

## 8. Auth (Stage 6 — implemented)

Single-user desktop phase with a server-side session, ready for multi-user
SaaS without reshaping `businessId` scoping:

- `POST /api/v1/auth/login {email, password}` → `200 {user, csrf_token,
  businesses}` + HttpOnly session cookie. Unknown email and wrong password
  are indistinguishable: uniform `401 code: "unauthorized"`,
  `message: "Invalid email or password."`. Brute force is throttled per
  identifier (sliding window) → `429 code: "rate_limited"`.
- Session: opaque random id in the cookie (`bos_session`, `HttpOnly`,
  `SameSite=Lax`); only its SHA-256 hash is stored server-side
  (`auth_sessions` row with `expires_at` + `revoked_at`). No JWT, no
  client-side state to forge. Fresh id on every login (no fixation).
- `GET /api/v1/auth/me` → `{user, csrf_token, businesses}` or `401`. The token
  is re-issued (same session-bound value, never rotated here) so a full page
  load — which wipes the browser's in-memory copy while the HttpOnly cookie
  survives — recovers mutation ability without re-login.
  `POST /api/v1/auth/logout` → revokes server-side, clears the cookie;
  like every state-changing request it requires the session-bound
  `X-CSRF-Token` header (constant-time compare), else `403`.
- Passwords: Argon2id only (`argon2-cffi` defaults), transparent rehash on
  login when parameters evolve. Verification failures never distinguish
  "unknown user" from "wrong password" and never leak hashes.
- Membership: `memberships (user_id, business_id, role owner|member)`.
  Every protected domain request checks the live membership set; the
  `business_id` comes ONLY from the URL path. Foreign or unknown business →
  uniform `404 code: "not_found"` (`"Business not found in scope."`) —
  tenant existence is never confirmed. Unauthenticated → `401`.
- No public registration: the first owner is created via server-side CLI
  (`backend/scripts/bootstrap_owner.py`); pre-existing businesses are NEVER
  auto-attached to a new user.
- CORS: tight origin allowlist (local Next dev + Tauri schemes, no `*`
  with credentials); `X-CSRF-Token` is an allowed header because mutations
  send it with `credentials: include`.

## 9. Amendments

| Date | Version | Change |
|---|---|---|
| 2026-10-09 | v1 | Initial freeze from `frontend/lib/domain/types.ts` |
| 2026-10-09 | v1.1 | §5.1: frozen spend semantics (derived aiSpent, recompute rule, rounding, currency) — clarification only, no rule changes |
| 2026-10-09 | v1.2 | §6: additive `provider_auth_error` code (provider credential failure, distinct from user `unauthorized`) — allowed additive addition, no existing rule changed |
| 2026-10-10 | v1.3 | §8: Stage 6 auth implemented (session cookie, CSRF, membership scoping, uniform 401/404, login throttle) — new `POST /auth/*` + `GET /auth/me` endpoints only; no renames, removals, or new required fields on existing shapes (additive, wire stays `v1`) |
| 2026-10-11 | v1.4 | Stage 7.0 read API: additive `GET` lists + single gets for business/projects/tasks/agents/approvals/activity/brain (`limit/offset` paging, `total`, stable `id` order, `?project_id` / `?category` filters) — new endpoints only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-11 | v1.5 | Stage 7.6 approval decisions: additive `POST .../approvals/{id}/approve|reject` (pending → approved|rejected via one conditional UPDATE + audit event in one txn; repeat = 200 `already_*`, opposite-after-terminal = 409 `conflict`, uniform 404 for foreign/missing; linked task untouched — executor re-reads the gate live) — new endpoints + `DecisionResponse` only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-11 | v1.6 | `GET /auth/me` re-issues the session `csrf_token` (same value, additive optional field): full page loads recover mutation ability without re-login — existing shapes, codes and wire `v1` unchanged |
| 2026-10-11 | v1.7 | Stage 8.1 Team-Lead intake: additive `POST .../briefs` (goal → 2–4 validated questions, ≤2 LLM generations, then 422 `validation_error`) + `GET .../briefs/{id}`, new `Brief` entity + `BriefStatus` — new endpoints and shapes only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-11 | v1.8 | Stage 8.2 answers & brief: additive `POST .../briefs/{id}/answers` (answers validated against stored questions, ≤2 summary generations, atomic `questions_ready` → `brief_ready` CAS; identical repeat = 200 saved result, different answers after finalize = 409 `conflict`) — new endpoint only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-11 | v1.9 | Stage 8.3 planning: additive `POST .../briefs/{id}/plan` (brief_ready only; ≤2 plan generations, ≤20 tasks; agents/refs/gates/budget validated; project + tasks + edges + approvals + `brief_ready` → `planned` CAS in one txn, full rollback on any error; concurrent planners serialize — loser re-reads the saved graph; planned repeat = 200 saved graph, no LLM; tasks never auto-started) + `PlanResponse` — new endpoint and shape only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-11 | v1.10 | Stage 8.4 client-driven execution: `TaskResponse` now carries `dependsOn` — contract §1 always required it, the read API omitted it (LIVE clients carried `[]`); additive optional field, one edge query per list — existing shapes, codes and wire `v1` unchanged |
| 2026-10-11 | v1.11 | Stage 8.5 atomic finalization: additive `POST .../briefs/{id}/finalize` (planned only, all tasks completed|failed, all approvals approved|rejected; ≤2 report generations outside the txn; validated report envelope + 0–5 Brain learnings + `planned` → `completed` CAS + audit event in one txn, full rollback on any error; concurrent finalizers serialize — loser re-reads the stored result; completed repeat = 200 stored result, no LLM) + `FinalizeResponse` + optional `Brief.finalReport` (migration 0004, nullable JSON) — new endpoint and shapes only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-12 | v1.13 | Stage 8 marketplace + hire: additive `GET .../marketplace` (paged catalog, stable slug order, `limit/offset`, per-business computed `hired`) + `GET .../marketplace/{slug}` (unknown slug → 404) + `POST .../marketplace/{slug}/hire` (employee only → 201 `AgentResponse`; pack → 422 `listing_not_hireable`; same slug twice in one business → 409 `already_hired`, enforced by app check + partial unique `(business_id, source_slug) WHERE source_slug IS NOT NULL` (migration 0005) so concurrent hirers serialize to one 201; agent + `system` audit event in one txn, full rollback on any error) + `MarketplaceResponse`/`MarketplaceListResponse` — hire mapping: uuid id, business scope, `source_slug`, name/role from listing, responsibilities←capabilities, permissions copy, status `available`, hired true, deterministic avatarHue from slug, cost_per_month best-effort `$NN` parse else 0.0 — new endpoints and shapes only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-12 | v1.12 | Stage 7.1 management writes: additive `POST .../brain` (manual knowledge entry, server-generated id, 201) + `PATCH .../projects/{id}` (title/goal/status/budget/aiBudget/deadline/teamIds/milestones) + `PATCH .../projects/{id}/tasks/{task_id}` (title/summary/etaMinutes/ownerAgentId/gateAt) — every mutation in one txn with a `system` audit event; executor/derived fields (task status/progress/costAccrued/attempts/approvalGateId/costRate, project aiSpent/progress) are rejected with 422, unknown enum literals with 422 `invalid_enum`, dangling refs with 422 `dangling_reference`; no DELETE, no schema change — new endpoints + `BrainCreate`/`ProjectPatch`/`TaskPatch` (request bodies only, not wire) ; existing shapes, codes and wire `v1` unchanged |
| 2026-10-14 | v1.14 | Stage 13 readiness: additive `GET /api/v1/readiness` (no auth — probes carry no credentials; 200 `{status: ready, database: ok}` iff a statement executes against the configured database, else 503 `{status: degraded, database: unconfigured\|unreachable}` with no internals) + `ReadinessResponse` — `/health` stays the dependency-free liveness probe; existing shapes, codes and wire `v1` unchanged |
| 2026-10-19 | v1.16 | Stage 15 pack installation: server-only manifests (`fitness-pack@1.0.0`) + additive `GET .../packs` (catalog with per-business `installed`/`installedVersion`), `GET .../packs/{slug}`, `GET .../packs/{slug}/preview` (planned agents/workflow + `canInstall`/`blockers`) and atomic `POST .../packs/{slug}/install` (201 first install; 200 `already_installed` same version; 409 `pack_version_conflict`; 422 `pack_unavailable` when catalog rows are missing; 422 `agent_disabled` when a reusable agent is disabled; unknown slug → uniform 404). One txn materializes the bundled agents (reusing existing `source_slug` rows unchanged), a planning workflow project + ordered `not_started` tasks with dependencies, receipt rows (`pack_installations`/`pack_installation_items`, migration 0006) and a `system` audit event, with full rollback on any error; concurrent installers serialize to one 201. `MarketplaceResponse` gains additive `installed`/`installedVersion`; new `Pack*Response` shapes only — existing shapes, codes and wire `v1` unchanged |
| 2026-10-19 | v1.17 | Stage 16 usage & billing foundation: §5.2 defines the append-only `usage_records` ledger (one canonical row per provider attempt, UNIQUE `idempotency_key`, no update/delete; unknown cost is `null`, never `0`; `cost_source` provider\|tariff\|unknown), the `budget_reservations` state machine (`active\|consumed\|ambiguous\|released\|written_off`, migration 0007), ledger-derived `task.costAccrued = SUM(confirmed cost)` feeding the unchanged §5.1 `aiSpent` recompute, and concurrency-safe budget enforcement committed in the SAME txn as the task claim (no txn held across the network call; a lost race rolls the claim back). Fail-closed: a budgeted project with no provable upper bound (needs a tariff AND capped output) is refused `budget_unpriced` (409) with no provider call unless `Project.allowUnpricedAi` opts into a `best_effort` reserve. Ambiguous (post-send unknown-outcome) holds are never auto-released or retried; recovery converts a stale `active` hold to `ambiguous` and releases `ambiguous` only when `usage_ambiguous_release_minutes > 0` (default 0). Additive `GET .../usage` (ledger rows + derived summary; `?project_id`/`?task_id`, `limit`/`offset`), additive `Project.allowUnpricedAi` (+ `ProjectPatch.allowUnpricedAi`), and new `Usage*Response` shapes only; existing shapes, codes and wire `v1` unchanged |
| 2026-10-19 | v1.18 | Stage 16.1 Team Lead & all-provider coverage: every billable provider call (task execution + `brief_questions`/`brief_summary`/`plan_generation`/`brief_finalize`) now writes the same append-only ledger through a new validated `call_kind` (migration 0008; legacy rows backfilled to `task_execution`, nullable for compatibility). Business-scoped Team Lead calls run before a project exists (`project_id`/`task_id` NULL) and never enter project spend; finalization is project-scoped, task-less and budget-enforced. §5.1/§5.2 `aiSpent` is the disjoint sum of task `costAccrued` plus confirmed task-less project-scoped usage, counted once; the reserve ceiling also counts task-less `estimated` exposure. Post-send malformed responses are never released or zeroed: priced `estimated` from the tariff bound when known, otherwise held `ambiguous`. `UsageItemResponse.callKind` and `UsageSummary.byCallKind` are additive; `FinalizeResponse.costs` now snapshots confirmed spend including this finalization and reports `ai_estimated` separately. Existing shapes, codes and wire `v1` unchanged |
| 2026-10-14 | v1.15 | Stage 14 agent lifecycle: `Agent.hired` is the active flag (hire → true; disable → false; rows, links and history are never deleted). Additive `POST .../agents/{id}/activate|disable` (idempotent 200 `AgentResponse` + `system` audit event in one txn; uniform 404 for foreign/missing). Staffing compatibility is scope + active only (no skill model exists): task owner / project `team_ids` / plan save with a disabled or foreign agent → 422 `agent_disabled` (new additive code) / `dangling_reference`; executor refuses new claims for disabled owners (`refused` + `agent_disabled`, 409) while in-flight runs and terminal/idempotent answers are untouched — new endpoints + one code only; existing shapes and wire `v1` unchanged |
| 2026-10-23 | v1.19 | Stage 18 S2 business AI budgets: additive `Business.aiBudget` + `Business.allowUnpricedAi` (migration 0009, defaults `0`/false) and owner-only `PATCH /businesses/{business_id}` (`aiBudget` must be `0` or at least `0.01` and fit `NUMERIC(12,2)`; invalid/overflowing values are `422 validation_error`; members are `403`, foreign businesses `404`). Business-scoped Team Lead calls (`questions`/`summary`/`planning`, `project_id IS NULL`) reserve against the business ceiling when `aiBudget > 0`; it counts all business confirmed/estimated usage plus all active/ambiguous holds, but only business-scoped calls are refused — project-scoped calls keep their project-only cap. `aiBudget = 0` preserves bare-ledger behavior; a budgeted business without a provable upper bound is refused `budget_unpriced` unless `allowUnpricedAi` opts into `best_effort`. `BusinessResponse` gains both fields additively; existing shapes, codes and wire `v1` unchanged |
| 2026-10-24 | v1.20 | Stage 18 S2.2 authorization: `PATCH .../projects/{id}` still permits members to edit non-budget metadata, but `aiBudget` or `allowUnpricedAi` requires business-owner membership; a mixed patch containing either budget-policy field is owner-only. Foreign business access remains uniform 404; no request/response shape or database change |
