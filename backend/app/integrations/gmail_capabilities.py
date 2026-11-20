"""Gmail capabilities for the existing Agent Capability Engine.

Reads execute immediately (subject to grants, connection ownership, scopes and
provider rate limits). Writes NEVER touch Gmail: they create a real pending
human approval plus an encrypted action row. Only the approval decision can
execute the external side effect (see `app.integrations.service`).

This keeps one execution path: the model still only requests capabilities;
the host authorizes, validates, audits and enforces approvals exactly as for
the built-in capabilities.
"""

import json
from typing import Any

from app.capabilities.registry import register_capabilities
from app.capabilities.types import (
    ApprovalPolicy,
    CapabilityContext,
    CapabilityError,
    CapabilityResult,
    CapabilityRisk,
    CapabilitySpec,
    text_limit,
)
from app.integrations import service
from app.integrations.crypto import cipher_for
from app.integrations.providers.gmail import GMAIL, GMAIL_SPEC, validate_message
from app.integrations.repositories import IntegrationActionRepository
from app.integrations.types import IntegrationError
from app.persistence.enums import IntegrationActionStatus
from app.persistence.exceptions import DanglingReferenceError
from app.persistence.repositories.concrete import ActivityRepository, ApprovalRepository

_approvals = ApprovalRepository()
_actions = IntegrationActionRepository()
_activity = ActivityRepository()

_WRITE_CAPABILITIES = frozenset({"gmail.drafts.create", "gmail.messages.send"})
_MAX_CONNECTION_ID = 128
_BODY_PREVIEW = 1200


def _connection_id(arguments: dict[str, Any]) -> str | None:
    value = arguments.get("connection_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CapabilityError("invalid_arguments", "connection_id must be a non-empty string.")
    value = value.strip()
    if len(value) > _MAX_CONNECTION_ID:
        raise CapabilityError("invalid_arguments", "connection_id is too long.")
    return value


def _provider_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in arguments.items() if key != "connection_id"}


def _write_guard(ctx: CapabilityContext) -> None:
    if ctx.attempt != 0:
        raise CapabilityError(
            "first_attempt_only",
            "Write capabilities are available only on the first attempt; this run is a retry.",
        )
    limit = int(getattr(ctx.settings, "execution_max_write_calls_per_run", 3))
    if ctx.writes_used >= limit:
        raise CapabilityError(
            "write_limit_reached",
            f"At most {limit} write capability calls are allowed per run.",
        )


async def _read(
    ctx: CapabilityContext, capability: str, arguments: dict[str, Any]
) -> CapabilityResult:
    connection_id = _connection_id(arguments)
    try:
        connection = await service.resolve_capability_connection(
            ctx.session,
            business_id=ctx.business_id,
            provider="gmail",
            capability=capability,
            connection_id=connection_id,
        )
        access_token = await service.ensure_access_token(ctx.session, ctx.settings, connection)
        result = await GMAIL.execute(
            ctx.settings,
            capability=capability,
            access_token=access_token,
            arguments=_provider_arguments(arguments),
        )
    except IntegrationError as exc:
        raise CapabilityError(exc.code, exc.message) from exc
    return CapabilityResult(
        capability=capability,
        ok=True,
        data={
            **result.data,
            "connectionId": connection.id,
            "account": connection.display_name,
            "untrusted": True,
        },
    )


async def _request_write(
    ctx: CapabilityContext, capability: str, arguments: dict[str, Any]
) -> CapabilityResult:
    _write_guard(ctx)
    connection_id = _connection_id(arguments)
    try:
        connection = await service.resolve_capability_connection(
            ctx.session,
            business_id=ctx.business_id,
            provider="gmail",
            capability=capability,
            connection_id=connection_id,
        )
        message = validate_message(arguments)
    except IntegrationError as exc:
        raise CapabilityError(exc.code, exc.message) from exc

    cipher = cipher_for(ctx.settings)
    payload = {
        **message,
        "connection_id": connection.id,
        "capability": capability,
    }
    encrypted_payload = cipher.encrypt(json.dumps(payload, ensure_ascii=False))
    recipient_text = ", ".join(message["to"][:5])
    if len(message["to"]) > 5:
        recipient_text += f" and {len(message['to']) - 5} more"
    action_label = "draft" if capability == "gmail.drafts.create" else "email"
    verb = "Create a Gmail draft" if action_label == "draft" else "Send an email"
    title = text_limit(f"Approve Gmail {action_label}: {message['subject']}", 300)
    what = text_limit(
        (
            f"{verb} from {connection.display_name} to {recipient_text}.\n"
            f"Subject: {message['subject']}\n\n{message['body'][:_BODY_PREVIEW]}"
        ),
        4000,
    )
    expected = (
        "The draft is created in Gmail only after you approve. Nothing is sent."
        if action_label == "draft"
        else "The email is sent only after you approve. Nothing is sent before that."
    )
    try:
        approval = await _approvals.create(
            ctx.session,
            ctx.business_id,
            {
                "project_id": ctx.project_id,
                "title": title,
                "requested_by": ctx.agent_name,
                "requested_by_agent_id": ctx.agent_id,
                "status": "pending",
                "what": what,
                "expected_result": expected,
                "cost": "",
                "affects": text_limit(recipient_text, 500),
                "rationale": (
                    "Requested through the Gmail integration. External side effects "
                    "always require explicit human approval."
                ),
                "gates_task_id": ctx.task_id,
            },
        )
        await _actions.create(
            ctx.session,
            ctx.business_id,
            {
                "project_id": ctx.project_id,
                "connection_id": connection.id,
                "approval_id": approval.id,
                "task_id": ctx.task_id,
                "capability": capability,
                "status": IntegrationActionStatus.PENDING.value,
                "payload_encrypted": encrypted_payload,
                "result": {},
            },
        )
    except DanglingReferenceError as exc:
        # The capability savepoint rolls the approval back too: no orphan gate.
        raise CapabilityError("invalid_reference", str(exc)) from exc
    await _activity.append(
        ctx.session,
        ctx.business_id,
        {
            "project_id": ctx.project_id,
            "agent_name": ctx.agent_name,
            "kind": "approval",
            "text": f"{ctx.agent_name} requested approval to {action_label} via Gmail: {title}",
        },
    )
    ctx.consume_write()
    return CapabilityResult(
        capability=capability,
        ok=True,
        data={
            "approvalId": approval.id,
            "status": approval.status,
            "connectionId": connection.id,
            "account": connection.display_name,
            "to": message["to"],
            "subject": message["subject"],
        },
        approval_id=approval.id,
    )


def capability_specs() -> list[CapabilitySpec]:
    specs: list[CapabilitySpec] = []
    for capability in GMAIL_SPEC.capabilities:
        is_write = capability.id in _WRITE_CAPABILITIES
        specs.append(
            CapabilitySpec(
                id=capability.id,
                title=capability.title,
                description=capability.description,
                risk=CapabilityRisk.WRITE if is_write else CapabilityRisk.READ,
                approval=ApprovalPolicy.REQUIRED if is_write else ApprovalPolicy.NONE,
                aliases=capability.aliases,
                parameters=capability.parameters,
            )
        )
    return specs


def _read_handler(capability: str):
    async def handler(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
        return await _read(ctx, capability, arguments)

    return handler


def _write_handler(capability: str):
    async def handler(ctx: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
        return await _request_write(ctx, capability, arguments)

    return handler


def register() -> tuple[list[CapabilitySpec], dict[str, Any]]:
    specs = capability_specs()
    handlers = {
        spec.id: (
            _write_handler(spec.id) if spec.id in _WRITE_CAPABILITIES else _read_handler(spec.id)
        )
        for spec in specs
    }
    register_capabilities(specs)
    return specs, handlers


__all__ = ["capability_specs", "register"]
