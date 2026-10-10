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
ENVELOPE_TYPE = "task_llm_result"

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
}


def build_system_prompt(agent_role: str, responsibilities: list[str]) -> str:
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
) -> dict[str, Any]:
    """Assemble the structured result envelope (not yet size-fitted)."""
    return {
        "type": ENVELOPE_TYPE,
        "task_id": task_id,
        "model": model,
        "summary": summary,
        "output": output,
        "usage": usage,
        "attempts": attempts,
    }


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
    fitted["summary"] = summary
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
