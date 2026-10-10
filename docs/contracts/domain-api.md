# Domain ↔ API contract (TypeScript ↔ Pydantic) — frozen at Stage 0

Source of truth for shapes: `frontend/lib/domain/types.ts`.
Source of truth for wire rules: this document.
Backend Pydantic models MUST mirror this document field-for-field.
Any deviation requires a contract amendment (new section below + minor version bump).

## 1. Entities and required fields

| Entity | TS type | Key fields (all required unless marked `?`) |
|---|---|---|
| Business | `Business` | `id, name, tagline, industry, location, ownerName, healthScore, healthBreakdown[{label, score}], metrics{revenue, revenueDelta, customers, customersDelta, conversion, conversionDelta, leads}, revenueSeries[{label, value}]` |
| Agent | `Agent` | `id, businessId, name, role, responsibilities[], tools[], permissions{read[], write[], actions[], denied[]}, status: AgentStatus, currentTask?, costPerMonth, performance{completionRate, avgMinutes, approvalRate, rating}, hired, avatarHue` |
| Task | `Task` | `id, businessId, projectId, title, ownerAgentId, status: TaskStatus, progress, dependsOn: ID[], costAccrued, etaMinutes, summary, attempts?, approvalGateId?, gateAt?, costRate?` |
| Project | `Project` | `id, businessId, title, goal, status: ProjectStatus, progress, budget, aiBudget, aiSpent, deadline, teamIds: ID[], milestones[{label, done}]` |
| Approval | `Approval` | `id, businessId, projectId, title, requestedBy, requestedByAgentId, ageLabel, status: ApprovalStatus, what, expectedResult, cost, affects, rationale, gatesTaskId?` |
| BrainEntry | `BrainEntry` | `id, businessId, category: BrainCategory, title, body, updatedLabel` |
| MarketplaceAgent | `MarketplaceAgent` | `slug, name, role, blurb, price, priceNote, rating, installs, badge?, capabilities[], permissions, category` |
| ActivityEvent | `ActivityEvent` | `id, at, agentName, text, kind: ActivityKind, cost?` |

## 2. Enumerations (exact wire literals — case-sensitive)

```text
AgentStatus  = available | thinking | working | waiting_dependency
             | waiting_approval | reviewing | completed | failed | escalated
TaskStatus   = not_started | working | waiting_dependency | waiting_assets
             | waiting_approval | completed | failed
ProjectStatus = planning | in_progress | review | completed
ApprovalStatus = pending | approved | rejected
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
  `project.aiSpent == Σ scoped tasks.costAccrued` is enforced server-side
  (today: enforced by the frontend engine + Vitest reconciliation test).
- `gateAt`: defaults to `100` when absent; `attempts`: defaults to `0`.

### 5.1 Spend semantics (frozen for Stage 2 persistence)

- **Counted records:** every `Task` row whose `(businessId, projectId)` matches
  the project scope, using each row's *current* `costAccrued`. No other table
  contributes; activity events and approvals never carry spend.
- **Derived, never incremented:** `project.aiSpent` is a cached aggregate.
  Writers MUST NOT `aiSpent += delta`. The only legal mutation is full
  recompute (`SUM(costAccrued)` over the scope) executed in the SAME
  transaction as the task-cost writes, with the project row locked
  (`SELECT … FOR UPDATE`), so concurrent writers serialize.
- **Double-count prevention:** recompute is idempotent — same task state
  always yields the same `aiSpent`. There is exactly one recompute path
  (repository helper); stage-4 orchestration calls it, never reimplements it.
- **Rounding and currency:** `Decimal`, quantum `0.01`, `ROUND_HALF_UP`
  (matches frontend `Math.round` tick-for-tick). Storage `NUMERIC(12,2)`.
  Single implied currency (USD); no currency column until multi-currency
  support (which then becomes a contract amendment, not silent behavior).

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
`dangling_reference`, `conflict`, `budget_exceeded`, `approval_required`,
`rate_limited`, `unauthorized`, `forbidden`, `internal`.
HTTP status mapping: 404 / 422 / 422 / 422 / 409 / 402? no — `budget_exceeded`
→ `409`; `approval_required` → `409`; `rate_limited` → `429`;
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

## 8. Auth (Stage 6 preview — reserved, not implemented)

Local single-user desktop phase: loopback-bound API, single local operator,
no login screen. Schema reserves `users` + `role` (`owner`) so multi-user
SaaS later does not require reshaping `businessId` scoping.

## 9. Amendments

| Date | Version | Change |
|---|---|---|
| 2026-10-09 | v1 | Initial freeze from `frontend/lib/domain/types.ts` |
| 2026-10-09 | v1.1 | §5.1: frozen spend semantics (derived aiSpent, recompute rule, rounding, currency) — clarification only, no rule changes |
| 2026-10-09 | v1.2 | §6: additive `provider_auth_error` code (provider credential failure, distinct from user `unauthorized`) — allowed additive addition, no existing rule changed |
