"""Prompt building + result-envelope fitting for single-task execution.

Pure functions (no DB, no network) — unit-testable in isolation.
The result schema is a service constant: tasks carry no schema field, so the
contract is untouched. Validation of model output happens in the adapter
(via this schema); nothing here claims compliance without that step.
"""

from typing import Any

MAX_SUMMARY_CHARS = 500
MAX_FINDINGS = 20
MAX_FINDING_CHARS = 300
MAX_ENVELOPE_CHARS = 8000
MAX_CAPABILITY_TRACE = 40
MAX_CAPABILITY_TRACE_CHARS = 120
ENVELOPE_TYPE = "task_llm_result"

# `tool_requests` is additive and optional: agents without a capability
# profile never see it in their prompt/schema and keep the legacy one-call
# result and bound.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
}

TOOL_REQUESTS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["capability"],
        "properties": {
            "capability": {"type": "string"},
            "arguments": {"type": "object"},
            "request_id": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
}


def result_schema(*, capabilities: bool) -> dict[str, Any]:
    """Return the output schema for this execution.

    Capability agents may request host-executed tools; legacy agents get the
    byte-identical pre-capability schema (and therefore the same reservation
    bound).
    """
    if not capabilities:
        return dict(RESULT_SCHEMA)
    return {
        **RESULT_SCHEMA,
        "properties": {**RESULT_SCHEMA["properties"], "tool_requests": TOOL_REQUESTS_SCHEMA},
    }


def build_system_prompt(
    agent_role: str,
    responsibilities: list[str],
    *,
    capability_block: str = "",
) -> str:
    lines = [
        f"You are the {agent_role} of an AI business team.",
        "You prepare a work draft for a human owner. Rules:",
        "- State facts and reasoning concisely; no flattery, no filler.",
        "- Never claim a real-world action was performed: text is a draft, not proof.",
        "- Never change statuses, permissions, approvals, prices, or finances.",
        "- Answer with a single JSON object matching the requested schema.",
    ]
    if responsibilities:
        lines.append("Your responsibilities: " + "; ".join(responsibilities[:8]) + ".")
    if capability_block:
        lines.append(capability_block)
    return "\n".join(lines)


def build_user_prompt(
    business_name: str, project_title: str, project_goal: str, task_title: str, task_summary: str
) -> str:
    parts = [
        f"Business: {business_name}",
        f"Project: {project_title}",
        f"Project goal: {project_goal}",
        f"Task: {task_title}",
    ]
    if task_summary:
        parts.append(f"Existing task notes: {task_summary[:1000]}")
    parts.append(
        "Produce the task result as JSON with a 'summary' string and an optional "
        "'findings' array of strings."
    )
    return "\n".join(parts)


def build_envelope(
    *,
    task_id: str,
    model: str,
    summary: str,
    output: Any,
    usage: dict[str, Any],
    attempts: int,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the structured result envelope (not yet size-fitted)."""
    envelope = {
        "type": ENVELOPE_TYPE,
        "task_id": task_id,
        "model": model,
        "summary": summary,
        "output": output,
        "usage": usage,
        "attempts": attempts,
    }
    if capabilities is not None:
        envelope["capabilities"] = capabilities
    return envelope


def _fit_capabilities(capabilities: dict[str, Any]) -> dict[str, Any]:
    fitted = dict(capabilities)
    calls = fitted.get("calls")
    if not isinstance(calls, list):
        fitted.pop("calls", None)
        return fitted
    trimmed: list[dict[str, Any]] = []
    for call in calls[:MAX_CAPABILITY_TRACE]:
        if not isinstance(call, dict):
            continue
        item = {
            "capability": str(call.get("capability", ""))[:MAX_CAPABILITY_TRACE_CHARS],
            "status": str(call.get("status", ""))[:32],
        }
        request_id = call.get("requestId")
        if request_id:
            item["requestId"] = str(request_id)[:MAX_CAPABILITY_TRACE_CHARS]
        error = call.get("error")
        if error:
            item["error"] = str(error)[:MAX_CAPABILITY_TRACE_CHARS]
        trimmed.append(item)
    fitted["calls"] = trimmed
    fitted["truncated"] = bool(fitted.get("truncated")) or len(calls) > len(trimmed)
    return fitted


def fit_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Shrink an envelope to MAX_ENVELOPE_CHARS without breaking JSON structure.

    Only ever truncates plain strings / drops whole array items — never cuts
    serialized JSON mid-structure. Deterministic: same input → same output.
    Raises ValueError if even the minimal envelope does not fit (invariant
    violation in our own caps, never silently stored).
    """
    import json as _json

    fitted: dict[str, Any] = dict(envelope)
    summary = str(fitted.get("summary", ""))[:MAX_SUMMARY_CHARS]
    findings = (
        fitted.get("output", {}).get("findings") if isinstance(fitted.get("output"), dict) else None
    )
    if isinstance(findings, list):
        trimmed = [str(f)[:MAX_FINDING_CHARS] for f in findings[:MAX_FINDINGS]]
        fitted["output"] = {**fitted["output"], "findings": trimmed}
    if isinstance(fitted.get("capabilities"), dict):
        fitted["capabilities"] = _fit_capabilities(fitted["capabilities"])
    fitted["summary"] = summary
    text = _json.dumps(fitted, ensure_ascii=False)
    if len(text) <= MAX_ENVELOPE_CHARS:
        return fitted
    capabilities = fitted.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("calls"):
        # Deterministic second step: keep counts, drop per-call detail.
        fitted["capabilities"] = {**capabilities, "calls": [], "truncated": True}
        text = _json.dumps(fitted, ensure_ascii=False)
        if len(text) <= MAX_ENVELOPE_CHARS:
            return fitted
    # Deterministic fallback: shrink summary to fit; structure stays valid.
    overhead = len(text) - len(summary)
    budget = MAX_ENVELOPE_CHARS - overhead
    if budget < 0:
        raise ValueError("envelope overhead exceeds size limit")
    fitted["summary"] = summary[:budget]
    text = _json.dumps(fitted, ensure_ascii=False)
    if len(text) > MAX_ENVELOPE_CHARS:
        raise ValueError("envelope does not fit size limit")
    return fitted
