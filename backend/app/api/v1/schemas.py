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
    cost_accrued: Decimal = Field(alias="costAccrued")
    eta_minutes: int = Field(alias="etaMinutes")
    summary: str
    attempts: int | None = None
    approval_gate_id: str | None = Field(default=None, alias="approvalGateId")
    gate_at: int | None = Field(default=None, alias="gateAt")
    cost_rate: Decimal | None = Field(default=None, alias="costRate")


class ExecutionCreate(BaseModel):
    timeout_s: float | None = Field(default=None, gt=0)


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


class DecisionResponse(Contracted):
    # approved|rejected: this call decided. already_*: idempotent repeat (200).
    # conflict: 409 body only — terminal for the other decision, with the
    # current status echoed; terminal states never reopen.
    outcome: str
    approval_id: str = Field(alias="approvalId")
    status: str | None = None
