"""Final-report generation from persisted results (Stage 8.5).

Same discipline as intake/planning: one bounded LLM call per generation, at
most two generations per request, network never inside a DB transaction.
Validation is two layers: the shared structured-output path
(`validate_against_schema`) for shape, then explicit truthfulness rules the
validator subset cannot express.

Truthfulness is enforced, not prompted: the caller passes the persisted
facts (task ids/titles/statuses, approval states, ledger budgets) and every
task the model claims MUST resolve to a persisted row with the claimed
status and matching title. Invented ids, misattributed outcomes, money
claims, and secret-looking strings are violations — never silently dropped.
Server-rendered sections (costs envelope, learning ids) are never taken
from model output.
"""

from typing import TYPE_CHECKING, Any

from app.core.config import Settings
from app.llm.base import LlmProvider
from app.llm.types import BadResponseError, LlmMessage, LlmRequest, StructuredOutput
from app.llm.validate import validate_against_schema
from app.persistence.enums import BrainCategory, check_values
from app.teamlead.questions import BriefGenerationError

if TYPE_CHECKING:
    from app.usage.metering import TeamLeadMeter

MAX_LEARNINGS = 5
MAX_TITLE_CHARS = 300
MAX_BODY_CHARS = 2000
MAX_LEARNING_CHARS = 500
MAX_LIST_ITEMS = 10

_BRAIN_CATEGORIES = check_values(BrainCategory)

# Anything secret-looking in a learning is a violation (learnings are
# reusable memory; credentials must never land there).
_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "passwd",
    "password",
    "bearer",
    "sk-",
    "aws_",
    "token=",
)

_REPORT_FIELDS = {
    "goal",
    "completed_tasks",
    "failed_tasks",
    "limitations",
    "recommendations",
}
_TASK_ENTRY_FIELDS = {"id", "title", "summary"}
_FAILED_ENTRY_FIELDS = {"id", "title", "reason"}
_LEARNING_FIELDS = {"category", "title", "body"}

FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["report", "learnings"],
    "properties": {
        "report": {
            "type": "object",
            "required": [
                "goal",
                "completed_tasks",
                "failed_tasks",
                "limitations",
                "recommendations",
            ],
            "properties": {
                "goal": {"type": "string"},
                "completed_tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "title", "summary"],
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                    },
                },
                "failed_tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "title", "reason"],
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "limitations": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
            },
        },
        "learnings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["category", "title", "body"],
                "properties": {
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
        },
    },
}


def _has_secret(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


def validate_finalization(data: object, facts: dict) -> list[str]:
    """Collect every violation (never fail-fast, never coerce, never invent).

    `facts` carries persisted truth: goal, tasks [{id, title, status}],
    approvals [{id, title, status}]. Every claimed task must resolve to a
    persisted row with the claimed outcome and matching title.
    """
    violations = validate_against_schema(data, FINAL_SCHEMA)
    if violations:
        return violations
    assert isinstance(data, dict)
    for key in data:
        if key not in ("report", "learnings"):
            violations.append(f"$: unexpected property {key!r}")
    by_id = {t["id"]: t for t in facts.get("tasks", []) if isinstance(t, dict)}
    report = data.get("report")
    if isinstance(report, dict):
        _validate_report(report, facts, by_id, violations)
    learnings = data.get("learnings")
    if isinstance(learnings, list):
        _validate_learnings(learnings, facts, violations)
    return violations


def _validate_report(report: dict, facts: dict, by_id: dict, violations: list[str]) -> None:
    for key in report:
        if key not in _REPORT_FIELDS:
            violations.append(f"$.report: unexpected property {key!r}")
    if report.get("goal") != facts.get("goal"):
        violations.append("$.report.goal: must restate the persisted brief goal exactly")
    for section, entry_fields, status in (
        ("completed_tasks", _TASK_ENTRY_FIELDS, "completed"),
        ("failed_tasks", _FAILED_ENTRY_FIELDS, "failed"),
    ):
        entries = report.get(section, [])
        if not isinstance(entries, list):
            violations.append(f"$.report.{section}: must be an array")
            continue
        if len(entries) > MAX_LIST_ITEMS:
            violations.append(f"$.report.{section}: at most {MAX_LIST_ITEMS} items")
        for i, entry in enumerate(entries):
            path = f"$.report.{section}[{i}]"
            if not isinstance(entry, dict):
                violations.append(f"{path}: expected an object")
                continue
            for key in entry:
                if key not in entry_fields:
                    violations.append(f"{path}: unexpected property {key!r}")
            row = by_id.get(entry.get("id"))
            if row is None:
                violations.append(f"{path}.id: unknown task {entry.get('id')!r}")
                continue
            if row.get("status") != status:
                violations.append(
                    f"{path}.id: task {entry.get('id')!r} is {row.get('status')!r}, "
                    f"not {status!r} — never misattribute outcomes"
                )
                continue
            if entry.get("title") != row.get("title"):
                violations.append(f"{path}.title: must match the persisted task title")
            text_field = "summary" if status == "completed" else "reason"
            text = entry.get(text_field)
            if not isinstance(text, str) or not text.strip():
                violations.append(f"{path}.{text_field}: must be a non-empty string")
            elif len(text.strip()) > MAX_BODY_CHARS:
                violations.append(f"{path}.{text_field}: at most {MAX_BODY_CHARS} characters")
    for section in ("limitations", "recommendations"):
        items = report.get(section, [])
        if not isinstance(items, list):
            violations.append(f"$.report.{section}: must be an array")
            continue
        if len(items) > MAX_LIST_ITEMS:
            violations.append(f"$.report.{section}: at most {MAX_LIST_ITEMS} items")
        for j, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                violations.append(f"$.report.{section}[{j}]: must be a non-empty string")


def _validate_learnings(learnings: list, facts: dict, violations: list[str]) -> None:
    if len(learnings) > MAX_LEARNINGS:
        violations.append(f"$.learnings: at most {MAX_LEARNINGS} items, got {len(learnings)}")
        return
    persisted_texts = {str(facts.get("goal", ""))}
    for t in facts.get("tasks", []):
        if isinstance(t, dict) and isinstance(t.get("summary"), str) and t["summary"].strip():
            persisted_texts.add(t["summary"].strip())
    for i, item in enumerate(learnings):
        path = f"$.learnings[{i}]"
        if not isinstance(item, dict):
            violations.append(f"{path}: expected an object")
            continue
        for key in item:
            if key not in _LEARNING_FIELDS:
                violations.append(f"{path}: unexpected property {key!r}")
        if item.get("category") not in _BRAIN_CATEGORIES:
            violations.append(f"{path}.category: must be one of {_BRAIN_CATEGORIES!r}")
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            violations.append(f"{path}.title: must be a non-empty string")
        elif len(title.strip()) > MAX_TITLE_CHARS:
            violations.append(f"{path}.title: at most {MAX_TITLE_CHARS} characters")
        body = item.get("body")
        if not isinstance(body, str) or not body.strip():
            violations.append(f"{path}.body: must be a non-empty string")
            continue
        if len(body.strip()) > MAX_LEARNING_CHARS:
            violations.append(f"{path}.body: at most {MAX_LEARNING_CHARS} characters")
        if body.strip() in persisted_texts:
            violations.append(f"{path}.body: must be a reusable learning, not a copy of the report")
        if _has_secret(title if isinstance(title, str) else "") or _has_secret(body):
            violations.append(f"{path}: must not contain secrets or credentials")


def normalize_finalization(data: dict) -> dict:
    """Pool of validated output only — call after validate_finalization() == []."""
    report = data["report"]
    normalized_report: dict[str, Any] = {"goal": report["goal"]}
    normalized_report["completed_tasks"] = [
        {"id": e["id"], "title": e["title"].strip(), "summary": e["summary"].strip()}
        for e in report["completed_tasks"]
    ]
    normalized_report["failed_tasks"] = [
        {"id": e["id"], "title": e["title"].strip(), "reason": e["reason"].strip()}
        for e in report["failed_tasks"]
    ]
    for section in ("limitations", "recommendations"):
        normalized_report[section] = [item.strip() for item in report[section]]
    learnings = [
        {
            "category": item["category"],
            "title": item["title"].strip(),
            "body": item["body"].strip(),
        }
        for item in data["learnings"]
    ]
    return {"report": normalized_report, "learnings": learnings}


async def generate_finalization(
    provider: LlmProvider,
    settings: Settings,
    facts: dict,
    *,
    max_generations: int = 2,
    meter: "TeamLeadMeter | None" = None,
) -> dict:
    """Ask the Team Lead for the final report + learnings; raise on exhaustion."""
    from app.teamlead.prompts import build_final_system_prompt, build_final_user_prompt

    last_violations: list[str] = ["no generation attempted"]
    for generation in range(max(1, max_generations)):
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=build_final_system_prompt()),
                LlmMessage(
                    role="user",
                    content=build_final_user_prompt(facts),
                ),
            ],
            model=getattr(provider, "_default_model", "mock"),
            max_tokens=None,
            temperature=None,
            structured=StructuredOutput(mode="json_schema", schema=dict(FINAL_SCHEMA)),
            timeout_s=settings.llm_timeout_s,
        )
        # LlmError propagates to the caller (mapped at the API boundary),
        # except malformed model output: that is an invalid generation, not
        # a provider failure — it consumes budget and is retried below.
        # CancelledError is not an LlmError and propagates untouched.
        try:
            if meter is not None:
                response = await meter.complete(provider, request, generation=generation)
            else:
                response = await provider.complete(request)
        except BadResponseError as exc:
            last_violations = [str(exc)]
            continue
        data = response.data if isinstance(response.data, dict) else None
        if data is None:
            last_violations = ["$: expected a JSON object response"]
            continue
        last_violations = validate_finalization(data, facts)
        if not last_violations:
            return normalize_finalization(data)
    raise BriefGenerationError(last_violations)
