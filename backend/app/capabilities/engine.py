"""Host-side capability executor.

One call = one short transaction:

1. prove the execution still owns the task (read-only, database statement time);
2. run the handler inside a savepoint (so a rejected call cannot leave a
   partial write behind);
3. re-prove ownership right before commit, with a shared row lock, so the
   lease cannot expire or move to a newer owner between the proof and commit;
4. append the capability audit event in the same transaction;
5. commit before the next provider call.

No transaction is ever held across a model call. The engine never retries a
write capability automatically: a duplicate side effect is worse than a clear
error, and the model can decide to retry in a later turn.
"""

import asyncio
import json
from typing import Any

from app.capabilities.builtin import HANDLERS
from app.capabilities.protocol import (
    capability_audit_text,
    failed_result,
    format_tool_results,
    parse_tool_requests,
)
from app.capabilities.registry import get_spec
from app.capabilities.types import (
    CapabilityCall,
    CapabilityContext,
    CapabilityError,
    CapabilityOwnershipLost,
    CapabilityResult,
    CapabilityRisk,
    CapabilitySpec,
)
from app.core.logging import get_logger
from app.llm.types import LlmUsage
from app.llm.validate import validate_against_schema
from app.persistence.repositories.concrete import ActivityRepository, TaskRepository

_activity = ActivityRepository()
_tasks = TaskRepository()
log = get_logger(__name__)

_RESULT_PREVIEW_MARGIN = 80


def _bound_data(result: CapabilityResult, char_cap: int) -> CapabilityResult:
    if result.data is None:
        return result
    try:
        text = json.dumps(result.data, ensure_ascii=False)
    except (TypeError, ValueError):
        result.ok = False
        result.error_code = "internal"
        result.error_message = "Capability result could not be serialized."
        result.data = None
        return result
    if len(text) <= char_cap:
        return result
    preview_chars = max(0, char_cap - _RESULT_PREVIEW_MARGIN)
    result.data = {"truncated": True, "preview": text[:preview_chars]}
    return result


def _write_policy_error(ctx: CapabilityContext, spec: CapabilitySpec) -> CapabilityResult | None:
    if spec.risk is not CapabilityRisk.WRITE:
        return None
    if ctx.attempt != 0:
        return failed_result(
            spec.id,
            "first_attempt_only",
            "Write capabilities are available only on the first attempt; this run is a retry.",
        )
    limit = int(getattr(ctx.settings, "execution_max_write_calls_per_run", 3))
    if ctx.writes_used >= limit:
        return failed_result(
            spec.id,
            "write_limit_reached",
            f"At most {limit} write capability calls are allowed per run.",
        )
    return None


class CapabilityExecutor:
    """Executes authorized calls for one task execution."""

    def __init__(self, specs: list[CapabilitySpec], settings: Any) -> None:
        self.specs = specs
        self.settings = settings
        self._granted_ids = {spec.id for spec in specs}

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.specs),
            "capabilities": [spec.wire() for spec in self.specs],
            "maxLlmCalls": int(getattr(self.settings, "execution_max_llm_calls", 1))
            if self.specs
            else 1,
        }

    def parse(self, data: Any) -> tuple[list[CapabilityCall], list[CapabilityResult]]:
        max_requests = int(getattr(self.settings, "execution_max_tool_requests_per_step", 4))
        calls, problems = parse_tool_requests(data, max_requests=max_requests)
        errors = [failed_result("<request>", "invalid_request", problem) for problem in problems]
        return calls, errors

    async def execute(self, ctx: CapabilityContext, call: CapabilityCall) -> CapabilityResult:
        spec = get_spec(call.capability)
        if spec is None or spec.id not in self._granted_ids:
            # The model may only call what its profile grants. Description of
            # the capability is deliberately not leaked for denied ids.
            return failed_result(
                call.capability,
                "not_authorized",
                "This capability is not granted to the agent.",
                request_id=call.request_id,
            )
        violations = validate_against_schema(call.arguments, spec.parameters)
        if violations:
            return failed_result(
                spec.id,
                "invalid_arguments",
                "Invalid arguments: " + "; ".join(violations[:5]),
                request_id=call.request_id,
            )
        policy_error = _write_policy_error(ctx, spec)
        if policy_error is not None:
            policy_error.request_id = call.request_id
            return policy_error

        handler = HANDLERS[spec.id]
        # Per-execution settings travel on the context; `self.settings` is only
        # the engine default used by describe/parse.
        call_settings = ctx.settings or self.settings
        timeout_s = float(getattr(call_settings, "execution_capability_timeout_s", 10.0))
        result: CapabilityResult | None = None
        try:
            async with ctx.session.begin():
                # The timeout covers the ownership proof and the handler's
                # savepoint, never the outer commit. A timed-out handler is
                # rolled back before the failure is audited, so a reported
                # timeout can never hide a committed side effect.
                try:
                    async with asyncio.timeout(timeout_s):
                        owned = await _tasks.owned_now(
                            ctx.session,
                            ctx.business_id,
                            ctx.task_id,
                            execution_generation=ctx.execution_generation,
                            lease_owner=ctx.lease_owner,
                        )
                        if not owned:
                            raise CapabilityOwnershipLost
                        try:
                            async with ctx.session.begin_nested():
                                result = await handler(ctx, call.arguments)
                        except CapabilityError as exc:
                            result = failed_result(
                                spec.id,
                                exc.code,
                                exc.message,
                                request_id=call.request_id,
                            )
                        # Re-prove ownership at the commit boundary: the lease
                        # may have expired (or ownership moved) while the
                        # handler ran. The shared row lock guarantees no newer
                        # owner is granted between this proof and the commit,
                        # so a side effect is never committed under ownership
                        # that is gone at commit time.
                        if not await _tasks.owned_now(
                            ctx.session,
                            ctx.business_id,
                            ctx.task_id,
                            execution_generation=ctx.execution_generation,
                            lease_owner=ctx.lease_owner,
                            lock=True,
                        ):
                            raise CapabilityOwnershipLost
                except TimeoutError:
                    result = failed_result(
                        spec.id,
                        "timeout",
                        "Capability timed out before its transaction committed.",
                        request_id=call.request_id,
                    )
                result.request_id = call.request_id
                await _activity.append(
                    ctx.session,
                    ctx.business_id,
                    {
                        "project_id": ctx.project_id,
                        "agent_name": ctx.agent_name,
                        "kind": "system",
                        "text": capability_audit_text(call, result),
                    },
                )
        except CapabilityOwnershipLost:
            raise
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return failed_result(
                spec.id,
                "timeout",
                "Capability transaction timed out before it committed.",
                request_id=call.request_id,
            )
        except Exception:
            # The per-call transaction rolled back. Report a safe failure; the
            # bounded loop can continue or the executor's persist path handles
            # a genuinely broken session.
            log.warning(
                "capability execution failed",
                exc_info=True,
                extra={
                    "context": {
                        "capability": spec.id,
                        "task_id": ctx.task_id,
                    }
                },
            )
            return failed_result(
                spec.id,
                "internal",
                "Capability execution failed safely; no partial write was kept.",
                request_id=call.request_id,
            )
        if result is None:  # defensive: the transaction committed no result
            return failed_result(
                spec.id,
                "internal",
                "Capability produced no result.",
                request_id=call.request_id,
            )
        return _bound_data(
            result,
            int(getattr(call_settings, "execution_capability_result_chars", 4000)),
        )

    def format_results(self, results: list[CapabilityResult]) -> str:
        return format_tool_results(
            results,
            char_cap=int(getattr(self.settings, "execution_capability_result_chars", 4000)),
        )


def aggregate_usage(usages: list[LlmUsage]) -> LlmUsage:
    """Sum provider usage across the bounded loop into one settlement shape.

    `reported` is true only when EVERY call reported usage; a partially
    reported run must never be presented as fully confirmed spend.
    """
    if not usages:
        return LlmUsage()
    prompt_tokens = sum(max(0, int(usage.prompt_tokens or 0)) for usage in usages)
    completion_tokens = sum(max(0, int(usage.completion_tokens or 0)) for usage in usages)
    cached = [usage.cached_tokens for usage in usages]
    reasoning = [usage.reasoning_tokens for usage in usages]
    costs = [usage.cost_estimate for usage in usages]
    return LlmUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cached_tokens=None if any(value is None for value in cached) else sum(cached),  # type: ignore[arg-type]
        reasoning_tokens=(
            None if any(value is None for value in reasoning) else sum(reasoning)  # type: ignore[arg-type]
        ),
        reported=all(bool(usage.reported) for usage in usages),
        cost_estimate=(
            None if any(value is None for value in costs) else sum(costs)  # type: ignore[arg-type]
        ),
    )


__all__ = ["CapabilityExecutor", "aggregate_usage"]
