"""Agent lifecycle endpoints (Stage 14).

POST .../agents/{agent_id}/activate (or /disable) flips the agent's active
flag (`hired`) inside the caller's business, plus the audit event in the
SAME transaction — either the flip lands with its audit trail, or nothing
is written at all. Unknown and foreign agents are a uniform 404.

Idempotency: repeating the same transition is success 200 with the current
row — toggles never conflict, and nothing else (tasks, reports, history)
is touched. Disabling refuses FUTURE staffing (task owner, project team,
plan save, execution claim) via `AgentDisabledError` → 422
`agent_disabled`; in-flight runs and stored history are never rewritten.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.executions import _factory, _settings
from app.api.v1.reads import _agent_response
from app.api.v1.schemas import (
    AgentCapabilitiesResponse,
    AgentCapabilityResponse,
    AgentResponse,
)
from app.capabilities.registry import resolve_grants
from app.core.auth import check_client_contract
from app.core.identity import Identity, require_business
from app.persistence.repositories.concrete import (
    ActivityRepository,
    AgentRepository,
    UserRepository,
)

router = APIRouter()
_agents = AgentRepository()
_activity = ActivityRepository()
_users = UserRepository()

# require_business is declared once, as a parameter: FastAPI evaluates it a
# single time per request (dependency cache), so enforcement is identical to
# the sibling routers — but here the handler also needs the Identity value
# to attribute the audit event to the deciding user.
_GUARD = [Depends(check_client_contract)]


async def _set_active(
    business_id: str,
    agent_id: str,
    hired: bool,
    identity: Identity,
    request: Request,
) -> AgentResponse:
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        async with session.begin():
            agent = await _agents.set_hired(session, business_id, agent_id, hired)
            if agent is None:
                raise HTTPException(status_code=404, detail="Agent not found in scope.")
            user = await _users.get(session, identity.user_id)
            actor = user.email if user is not None else identity.user_id
            verb = "activated" if hired else "disabled"
            await _activity.append(
                session,
                business_id,
                {
                    "agent_name": actor,
                    "kind": "system",
                    "text": f"Agent {agent_id} {verb} by {actor}.",
                },
            )
            return _agent_response(agent, settings.contract_version)


@router.post(
    "/businesses/{business_id}/agents/{agent_id}/activate",
    response_model=AgentResponse,
    dependencies=_GUARD,
)
async def activate_agent(
    business_id: str,
    agent_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    return await _set_active(business_id, agent_id, True, identity, request)


@router.post(
    "/businesses/{business_id}/agents/{agent_id}/disable",
    response_model=AgentResponse,
    dependencies=_GUARD,
)
async def disable_agent(
    business_id: str,
    agent_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    return await _set_active(business_id, agent_id, False, identity, request)


@router.get(
    "/businesses/{business_id}/agents/{agent_id}/capabilities",
    response_model=AgentCapabilitiesResponse,
    dependencies=_GUARD,
)
async def get_agent_capabilities(
    business_id: str,
    agent_id: str,
    request: Request,
    identity: Identity = Depends(require_business),  # noqa: B008 - FastAPI idiom
):
    """Read-only introspection of the capabilities resolved for one agent.

    The profile columns stay the single source of truth: this endpoint only
    reports what execution would grant, never grants anything itself. An
    unknown agent is a uniform 404 (same rule as lifecycle); a disabled agent
    still reports its stored profile so an owner can inspect it before
    re-activating.
    """
    settings = _settings(request)
    factory = _factory(request)
    async with factory() as session:
        agent = await _agents.get(session, business_id, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found in scope.")
        specs = (
            resolve_grants(agent.tools, agent.permissions)
            if settings.execution_capabilities_enabled
            else []
        )
        return AgentCapabilitiesResponse(
            agentId=agent.id,
            enabled=bool(specs),
            maxLlmCalls=settings.execution_max_llm_calls if specs else 1,
            capabilities=[
                AgentCapabilityResponse(
                    id=spec.id,
                    title=spec.title,
                    description=spec.description,
                    risk=spec.risk.value,
                    approvalRequired=spec.approval.value == "required",
                )
                for spec in specs
            ],
            contract=settings.contract_version,
        )
