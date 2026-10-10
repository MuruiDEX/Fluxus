"""Stage 5 API schemas — contract §1 shapes, nothing more.

Field names use the contract's camelCase wire format. Only fields the
contract defines are exposed; internal columns (created_at/updated_at) and
secrets never appear. No schema change vs the frozen contract.
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
