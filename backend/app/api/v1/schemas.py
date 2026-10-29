"""Stage 5 API schemas — contract §1 shapes, nothing more.

Field names use the contract's camelCase wire format. Only fields the
contract defines are exposed; internal columns (created_at/updated_at,
notes) and secrets never appear. No schema change vs the frozen contract.
Stage 7.0 adds read shapes for the entities §1 already froze — same rules.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Contracted(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    contract: str = "v1"


class TaskResponse(Contracted):
    id: str
    business_id: str = Field(alias="businessId")
    project_id: str = Field(alias="projectId")
    title: str
    owner_agent_id: str = Field(alias="ownerAgentId")
    status: str
    progress: int
    # Contract §1 always required dependsOn; the read API omitted it until
    # amendment v1.10. Additive optional field — wire stays v1.
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    cost_accrued: Decimal = Field(alias="costAccrued")
    eta_minutes: int = Field(alias="etaMinutes")
    summary: str
    attempts: int | None = None
    approval_gate_id: str | None = Field(default=None, alias="approvalGateId")
    gate_at: int | None = Field(default=None, alias="gateAt")
    cost_rate: Decimal | None = Field(default=None, alias="costRate")


class ExecutionCreate(BaseModel):
    timeout_s: float | None = Field(default=None, gt=0)


class BriefQuestionResponse(BaseModel):
    id: str
    text: str
    options: list[str] | None = None


class BriefResponse(Contracted):
    id: str
    business_id: str = Field(alias="businessId")
    goal: str
    status: str
    questions: list[BriefQuestionResponse] = Field(default_factory=list)
    answers: list = Field(default_factory=list)
    summary: dict | None = None
    project_id: str | None = Field(default=None, alias="projectId")
    # Stage 8.5 finalization envelope (report + learnings + costs); NULL until
    # the brief completes. Additive optional field — wire stays v1.
    final_report: dict | None = Field(default=None, alias="finalReport")


class ExecutionError(BaseModel):
    code: str
    message: str


class ExecutionResponse(Contracted):
    outcome: str
    task_id: str = Field(alias="taskId")
    status: str | None = None
    attempts: int | None = None
    llm_attempts: int = Field(default=0, alias="llmAttempts")
    usage: dict = Field(default_factory=dict)
    error: ExecutionError | None = None


class RecoveryResponse(Contracted):
    outcome: str  # recovered | not_stale | not_found
    task_id: str = Field(alias="taskId")
    status: str | None = None


class BusinessResponse(Contracted):
    id: str
    name: str
    tagline: str = ""
    industry: str = ""
    location: str = ""
    owner_name: str = Field(default="", alias="ownerName")
    health_score: int = Field(default=0, alias="healthScore")
    health_breakdown: list = Field(default_factory=list, alias="healthBreakdown")
    metrics: dict = Field(default_factory=dict)
    revenue_series: list = Field(default_factory=list, alias="revenueSeries")
    # Stage 18 S2: business-scoped Team Lead budget policy. Additive fields;
    # wire stays v1.
    ai_budget: Decimal = Field(default=Decimal("0"), alias="aiBudget")
    allow_unpriced_ai: bool = Field(default=False, alias="allowUnpricedAi")


class ProjectResponse(Contracted):
    id: str
    business_id: str = Field(alias="businessId")
    title: str
    goal: str = ""
    status: str
    progress: int
    budget: Decimal
    ai_budget: Decimal = Field(alias="aiBudget")
    ai_spent: Decimal = Field(alias="aiSpent")
    deadline: str = ""
    team_ids: list = Field(default_factory=list, alias="teamIds")
    milestones: list = Field(default_factory=list)
    # Stage 16 (contract v1.17): opt-in to best-effort AI calls without a
    # priceable tariff. Default false: a budgeted project fails closed.
    allow_unpriced_ai: bool = Field(default=False, alias="allowUnpricedAi")


class AgentResponse(Contracted):
    id: str
    business_id: str = Field(alias="businessId")
    name: str
    role: str
    responsibilities: list = Field(default_factory=list)
    tools: list = Field(default_factory=list)
    permissions: dict = Field(default_factory=dict)
    status: str
    current_task: str | None = Field(default=None, alias="currentTask")
    cost_per_month: float = Field(default=0.0, alias="costPerMonth")
    performance: dict = Field(default_factory=dict)
    hired: bool = True
    avatar_hue: int = Field(default=200, alias="avatarHue")


class ApprovalResponse(Contracted):
    id: str
    business_id: str = Field(alias="businessId")
    project_id: str = Field(alias="projectId")
    title: str
    requested_by: str = Field(default="", alias="requestedBy")
    requested_by_agent_id: str = Field(alias="requestedByAgentId")
    age_label: str = Field(default="", alias="ageLabel")
    status: str
    what: str = ""
    expected_result: str = Field(default="", alias="expectedResult")
    cost: str = ""
    affects: str = ""
    rationale: str = ""
    gates_task_id: str | None = Field(default=None, alias="gatesTaskId")


class ActivityResponse(Contracted):
    id: str
    # Wire carries the server timestamp as RFC 3339 (§4); the "2h ago"
    # display string is derived client-side and never stored.
    at: str = ""
    agent_name: str = Field(default="", alias="agentName")
    text: str = ""
    kind: str
    cost: Decimal | None = None


class BrainResponse(Contracted):
    id: str
    business_id: str = Field(alias="businessId")
    category: str
    title: str
    body: str = ""
    updated_label: str = Field(default="", alias="updatedLabel")


class BusinessListResponse(Contracted):
    items: list[BusinessResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class ProjectListResponse(Contracted):
    items: list[ProjectResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class TaskListResponse(Contracted):
    items: list[TaskResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class AgentListResponse(Contracted):
    items: list[AgentResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class ApprovalListResponse(Contracted):
    items: list[ApprovalResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class ActivityListResponse(Contracted):
    items: list[ActivityResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class BrainListResponse(Contracted):
    items: list[BrainResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class UsageItemResponse(BaseModel):
    """One canonical ledger row (contract v1.18). `costAmount` is NULL when the
    cost is unknown — unknown is never reported as zero. `callKind` names the
    operation that produced the call (Stage 16.1)."""

    id: str
    business_id: str = Field(alias="businessId")
    project_id: str | None = Field(default=None, alias="projectId")
    task_id: str | None = Field(default=None, alias="taskId")
    agent_id: str | None = Field(default=None, alias="agentId")
    attempt: int = 0
    provider: str
    model: str
    call_kind: str | None = Field(default=None, alias="callKind")
    provider_request_id: str | None = Field(default=None, alias="providerRequestId")
    prompt_tokens: int = Field(default=0, alias="promptTokens")
    completion_tokens: int = Field(default=0, alias="completionTokens")
    total_tokens: int = Field(default=0, alias="totalTokens")
    cached_tokens: int | None = Field(default=None, alias="cachedTokens")
    reasoning_tokens: int | None = Field(default=None, alias="reasoningTokens")
    cost_amount: Decimal | None = Field(default=None, alias="costAmount")
    currency: str = "USD"
    cost_source: str = Field(alias="costSource")
    usage_status: str = Field(alias="usageStatus")
    tariff_version: str | None = Field(default=None, alias="tariffVersion")
    occurred_at: str = Field(alias="occurredAt")


class UsageReservationSummary(BaseModel):
    active: Decimal = Decimal("0")
    ambiguous: Decimal = Decimal("0")
    consumed: Decimal = Decimal("0")
    released: Decimal = Decimal("0")
    written_off: Decimal = Field(default=Decimal("0"), alias="writtenOff")


class UsageCallKindSummary(BaseModel):
    """Per-call-kind money breakdown (Stage 16.1). Confirmed and estimated stay
    apart; `unknownCount` counts unpriceable rows (never reported as cost)."""

    confirmed_cost: Decimal = Field(default=Decimal("0"), alias="confirmedCost")
    estimated_cost: Decimal = Field(default=Decimal("0"), alias="estimatedCost")
    unknown_count: int = Field(default=0, alias="unknownCount")


class UsageSummary(BaseModel):
    confirmed_cost: Decimal = Field(default=Decimal("0"), alias="confirmedCost")
    estimated_cost: Decimal = Field(default=Decimal("0"), alias="estimatedCost")
    unknown_count: int = Field(default=0, alias="unknownCount")
    by_call_kind: dict[str, UsageCallKindSummary] = Field(default_factory=dict, alias="byCallKind")
    reservations: UsageReservationSummary = Field(default_factory=UsageReservationSummary)


class UsageListResponse(Contracted):
    items: list[UsageItemResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
    summary: UsageSummary = Field(default_factory=UsageSummary)


class DecisionResponse(Contracted):
    # approved|rejected: this call decided. already_*: idempotent repeat (200).
    # conflict: 409 body only — terminal for the other decision, with the
    # current status echoed; terminal states never reopen.
    outcome: str
    approval_id: str = Field(alias="approvalId")
    status: str | None = None


class BrainCreate(BaseModel):
    """Stage 7.1: manual knowledge entry. Id is always server-generated."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    category: str
    title: str = Field(min_length=1, max_length=300)
    body: str = ""


class BusinessPatch(BaseModel):
    """Stage 18 S2: business-scoped AI budget policy.

    Only the fields the business-scoped Team Lead meter consults are writable;
    all other business columns stay server-owned.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ai_budget: Decimal | None = Field(default=None, alias="aiBudget")
    allow_unpriced_ai: bool | None = Field(default=None, alias="allowUnpricedAi")


class ProjectPatch(BaseModel):
    """Stage 7.1: project metadata patch. Executor/derived fields are NOT
    accepted here — extra keys (status transitions excluded) are 422, so
    ai_spent/progress can never be smuggled through this path."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    goal: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    budget: Decimal | None = None
    ai_budget: Decimal | None = Field(default=None, alias="aiBudget")
    deadline: str | None = Field(default=None, max_length=64)
    team_ids: list[str] | None = Field(default=None, alias="teamIds")
    milestones: list | None = None
    allow_unpriced_ai: bool | None = Field(default=None, alias="allowUnpricedAi")


class TaskPatch(BaseModel):
    """Stage 7.1: task metadata patch. Execution-owned fields (status,
    progress, cost_accrued, attempts, approval_gate_id, cost_rate,
    project_id) are NOT accepted — extra keys are 422, so the executor's
    claim semantics and the ai_spent recompute path cannot be bypassed."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = None
    eta_minutes: int | None = Field(default=None, alias="etaMinutes", ge=0)
    owner_agent_id: str | None = Field(default=None, alias="ownerAgentId")
    gate_at: int | None = Field(default=None, alias="gateAt", ge=0, le=100)


class PlanResponse(Contracted):
    # Stage 8.3: the materialized work plan — saved project graph + gate
    # approvals for a planned brief. Additive shape; wire stays v1.
    brief_id: str = Field(alias="briefId")
    project: ProjectResponse
    tasks: list[TaskResponse] = Field(default_factory=list)
    approvals: list[ApprovalResponse] = Field(default_factory=list)


class FinalizeLearning(BaseModel):
    id: str
    category: str
    title: str
    body: str


class FinalizeResponse(Contracted):
    # Stage 8.5: the persisted finalization result — validated report from
    # saved rows, stored Brain learnings, and server-rendered ledger costs.
    # Additive shape; wire stays v1.
    brief_id: str = Field(alias="briefId")
    project_id: str = Field(alias="projectId")
    status: str
    report: dict = Field(default_factory=dict)
    learnings: list[FinalizeLearning] = Field(default_factory=list)
    costs: dict = Field(default_factory=dict)


class MarketplaceResponse(Contracted):
    # Stage 8.1: one catalog row. The wire id is `slug` (contract §1, §3 —
    # kebab-case, immutable); `hired` is computed per requesting business.
    id: str = Field(alias="slug")
    name: str
    role: str = ""
    blurb: str = ""
    price: str = ""
    price_note: str = Field(default="", alias="priceNote")
    rating: float = 0.0
    installs: str = ""
    badge: str | None = None
    capabilities: list = Field(default_factory=list)
    permissions: dict = Field(default_factory=dict)
    category: str
    hired: bool = False
    # Stage 15: computed per business for pack listings; employees stay false.
    installed: bool = False
    installed_version: str | None = Field(default=None, alias="installedVersion")


class MarketplaceListResponse(Contracted):
    items: list[MarketplaceResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class PackAgentResponse(BaseModel):
    slug: str
    name: str
    role: str
    capabilities: list = Field(default_factory=list)
    agent_id: str | None = Field(default=None, alias="agentId")
    action: str = "create"  # create | reuse | blocked


class PackWorkflowTaskResponse(BaseModel):
    key: str
    title: str
    owner_agent_slug: str = Field(alias="ownerAgentSlug")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")


class PackWorkflowResponse(BaseModel):
    ref: str
    title: str
    goal: str
    project_id: str | None = Field(default=None, alias="projectId")
    tasks: list[PackWorkflowTaskResponse] = Field(default_factory=list)


class PackResponse(Contracted):
    slug: str
    version: str
    name: str
    description: str
    blurb: str = ""
    price: str = ""
    price_note: str = Field(default="", alias="priceNote")
    rating: float = 0.0
    installs: str = ""
    installed: bool = False
    installed_version: str | None = Field(default=None, alias="installedVersion")
    installed_at: str | None = Field(default=None, alias="installedAt")
    agents: list[PackAgentResponse] = Field(default_factory=list)
    workflow: PackWorkflowResponse | None = None


class PackListResponse(Contracted):
    items: list[PackResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class PackBlockerResponse(BaseModel):
    code: str
    message: str


class PackPreviewResponse(Contracted):
    pack: PackResponse
    can_install: bool = Field(alias="canInstall")
    blockers: list[PackBlockerResponse] = Field(default_factory=list)


class PackInstallResponse(Contracted):
    outcome: str  # installed | already_installed
    pack: PackResponse
    installation_id: str = Field(alias="installationId")
    installed_at: str = Field(alias="installedAt")
    agents: list[PackAgentResponse] = Field(default_factory=list)
    project: ProjectResponse | None = None
    tasks: list[TaskResponse] = Field(default_factory=list)
