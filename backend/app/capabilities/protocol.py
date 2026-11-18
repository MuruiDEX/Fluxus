"""Model-facing capability protocol: prompt block, request parsing, results.

The protocol is structured JSON, not provider-native tool calling, so every
adapter (including the deterministic MockProvider) shares one tested path.
The model only proposes `tool_requests`; the host decides whether each call is
authorized and what side effects happen.
"""

import json
from typing import Any

from app.capabilities.registry import get_spec
from app.capabilities.types import (
    MAX_CAPABILITY_ARGUMENT_CHARS,
    MAX_CAPABILITY_NAME_CHARS,
    MAX_CAPABILITY_REASON_CHARS,
    MAX_CAPABILITY_REQUEST_ID_CHARS,
    CapabilityCall,
    CapabilityResult,
    CapabilitySpec,
    text_limit,
)

TOOL_REQUESTS_KEY = "tool_requests"
MAX_PROMPT_SCHEMA_CHARS = 800


def build_capability_block(specs: list[CapabilitySpec], max_llm_calls: int) -> str:
    """System-prompt instructions for the bounded capability loop."""
    lines = [
        "You have server-enforced capabilities. You cannot execute them "
        "yourself; you request them and the host performs them.",
        f"You may use at most {max_llm_calls} model turns for this task, "
        "including the final answer.",
        "To request capabilities, return a JSON object with a 'summary' and a "
        f"'{TOOL_REQUESTS_KEY}' array. Each request is "
        '{"capability": "<id>", "arguments": {...}, "reason": "..."}.',
        "Only the ids listed below are allowed. Invalid, denied or malformed "
        "requests are answered with an error object; fix them or continue.",
        "The host returns tool results in the next user message. Never invent "
        "tool output and never repeat an identical successful call.",
        "Write capabilities (approvals.request, task.delegate) create REAL "
        "pending records for a human and are available only on the first "
        "attempt. They never approve, execute or spend anything themselves.",
        "When you have enough information, return the final JSON result "
        f"without a '{TOOL_REQUESTS_KEY}' array.",
        "Available capabilities (arguments use these exact field names):",
    ]
    for spec in specs:
        gate = " [human approval record]" if spec.risk.value == "write" else ""
        lines.append(f"- {spec.id} ({spec.risk.value}){gate}: {spec.description}")
        schema_text = json.dumps(spec.parameters, ensure_ascii=False, separators=(",", ":"))
        if len(schema_text) > MAX_PROMPT_SCHEMA_CHARS:
            schema_text = schema_text[:MAX_PROMPT_SCHEMA_CHARS] + "..."
        lines.append(f"  arguments: {schema_text}")
    return "\n".join(lines)


def parse_tool_requests(data: Any, *, max_requests: int) -> tuple[list[CapabilityCall], list[str]]:
    """Parse and defensively bound the model's requests.

    Returns `(calls, problems)`. Problems are safe strings that are fed back to
    the model as failed tool results; they never abort the run by themselves.
    """
    if not isinstance(data, dict):
        return [], []
    raw = data.get(TOOL_REQUESTS_KEY)
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["tool_requests must be an array of request objects."]

    calls: list[CapabilityCall] = []
    problems: list[str] = []
    for index, item in enumerate(raw):
        if len(calls) >= max_requests:
            problems.append(
                f"Request #{index + 1} ignored: at most {max_requests} capability "
                "requests are allowed per turn."
            )
            continue
        if not isinstance(item, dict):
            problems.append(f"Request #{index + 1} ignored: expected an object.")
            continue
        capability = item.get("capability")
        if not isinstance(capability, str) or not capability.strip():
            problems.append(f"Request #{index + 1} ignored: missing capability id.")
            continue
        capability = text_limit(capability.strip(), MAX_CAPABILITY_NAME_CHARS)
        if get_spec(capability) is None:
            problems.append(f"Request #{index + 1} ignored: unknown capability {capability!r}.")
            continue
        arguments = item.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            problems.append(f"Request #{index + 1} ignored: arguments must be an object.")
            continue
        try:
            args_json = json.dumps(arguments, ensure_ascii=False)
        except (TypeError, ValueError):
            problems.append(f"Request #{index + 1} ignored: arguments are not JSON.")
            continue
        if len(args_json) > MAX_CAPABILITY_ARGUMENT_CHARS:
            problems.append(
                f"Request #{index + 1} ignored: arguments exceed "
                f"{MAX_CAPABILITY_ARGUMENT_CHARS} characters."
            )
            continue
        request_id = item.get("request_id")
        if not isinstance(request_id, str):
            request_id = ""
        request_id = text_limit(request_id.strip(), MAX_CAPABILITY_REQUEST_ID_CHARS)
        reason = item.get("reason")
        if not isinstance(reason, str):
            reason = ""
        calls.append(
            CapabilityCall(
                capability=capability,
                arguments=arguments,
                request_id=request_id,
                reason=text_limit(reason.strip(), MAX_CAPABILITY_REASON_CHARS),
            )
        )
    return calls, problems


def failed_result(
    capability: str,
    code: str,
    message: str,
    *,
    request_id: str = "",
) -> CapabilityResult:
    return CapabilityResult(
        capability=capability,
        ok=False,
        error_code=code,
        error_message=message,
        request_id=request_id,
    )


def _compact(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _bounded_json(payload: Any, char_cap: int) -> str:
    text = _compact(payload)
    if len(text) <= char_cap:
        return text
    # Deterministic fallback: keep the envelope, replace payload with a notice.
    fallback = {
        "toolResults": [
            {
                "notice": "Tool results were truncated to fit the result size limit.",
                "chars": len(text),
            }
        ]
    }
    return _compact(fallback)


def format_tool_results(results: list[CapabilityResult], *, char_cap: int) -> str:
    """Build the bounded user message that carries tool output back.

    Multiple calls in one step must not make every result disappear when the
    combined JSON exceeds the cap. A deterministic fair split keeps each
    result's status/ids and a bounded data preview; only if even that cannot
    fit does the message collapse to a size notice.
    """
    payload = {"toolResults": [result.wire() for result in results]}
    text = _compact(payload)
    if len(text) <= char_cap:
        return text
    if not results:
        return _bounded_json(payload, char_cap)

    # Half the fair share leaves room for JSON quoting/escaping and the
    # per-result envelope (capability, ok, ids, error object).
    share = max(80, (char_cap - 512) // (2 * len(results)))
    trimmed: list[dict[str, Any]] = []
    for result in results:
        wire = result.wire()
        data = wire.pop("data", None)
        if data is not None:
            data_text = _compact(data)
            wire["data"] = {
                "truncated": len(data_text) > share,
                "preview": data_text[:share],
            }
        trimmed.append(wire)
    text = _compact({"toolResults": trimmed})
    if len(text) <= char_cap:
        return text
    return _bounded_json(payload, char_cap)


def capability_audit_text(call: CapabilityCall, result: CapabilityResult) -> str:
    """Concise, non-sensitive audit line for the Activity feed."""
    status = "ok" if result.ok else f"error:{result.error_code or 'unknown'}"
    extra = ""
    if result.approval_id:
        extra += f" approval={result.approval_id}"
    if result.task_id:
        extra += f" task={result.task_id}"
    return f"Capability {call.capability} → {status}{extra}"


__all__ = [
    "TOOL_REQUESTS_KEY",
    "build_capability_block",
    "capability_audit_text",
    "failed_result",
    "format_tool_results",
    "parse_tool_requests",
]
