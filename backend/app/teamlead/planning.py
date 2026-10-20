"""Work-plan generation from an approved brief (Stage 8.3).

Same discipline as intake: one bounded LLM call per generation, at most two
generations per request, network never inside a DB transaction. Validation is
two layers: the shared structured-output path (`validate_against_schema`) for
shape, then explicit business rules the validator subset cannot express
(count bounds, key graph acyclicity, money/gate ranges, protected-field
rejection — minLength-style keywords are outside its subset and would be
silently ignored).

Agent-existence checks are deliberately NOT here: they need the database and
run at save time inside the planning transaction (agents may change between
generation and save). Anything the model emits for a protected column
(status, ids, progress, money-as-text, gates-by-id) is a violation, never
silently dropped — the model must not steer protected state, even by
accident.
"""

import math
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from app.core.config import Settings
from app.llm.base import LlmProvider
from app.llm.types import BadResponseError, LlmMessage, LlmRequest, StructuredOutput
from app.llm.validate import validate_against_schema
from app.teamlead.prompts import MAX_PLAN_TASKS, PLAN_SCHEMA
from app.teamlead.questions import BriefGenerationError

if TYPE_CHECKING:
    from app.usage.metering import TeamLeadMeter

# Allowlist per object: anything else is a violation (fail), never a silent
# drop — protected columns must not be steerable through the plan.
_PROJECT_FIELDS = {"title", "goal", "budget", "ai_budget", "deadline"}
_TASK_FIELDS = {
    "key",
    "title",
    "owner_agent_id",
    "summary",
    "eta_minutes",
    "gate_at",
    "depends_on",
    "gate",
}
_GATE_FIELDS = {
    "title",
    "requested_by_agent_id",
    "requested_by",
    "what",
    "expected_result",
    "cost",
    "affects",
    "rationale",
}

_MAX_TITLE_CHARS = 300  # projects.title / tasks.title / approvals.title
_MAX_DEADLINE_CHARS = 64  # projects.deadline, verbatim display string
_MAX_COST_CHARS = 200  # approvals.cost display string
_MAX_BY_CHARS = 200  # approvals.requested_by display string


def _is_money(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    try:
        return Decimal(str(value)) >= 0
    except InvalidOperation:
        return False


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_plan(data: object) -> list[str]:
    """Collect every violation (never fail-fast, never coerce, never invent)."""
    violations = validate_against_schema(data, PLAN_SCHEMA)
    if violations:
        return violations
    assert isinstance(data, dict)
    for key in data:
        if key not in ("project", "tasks"):
            violations.append(f"$: unexpected property {key!r}")
    project = data.get("project")
    tasks = data.get("tasks")
    if isinstance(project, dict):
        _validate_project(project, violations)
    if isinstance(tasks, list):
        _validate_tasks(tasks, violations)
    return violations


def _validate_project(project: dict, violations: list[str]) -> None:
    for key in project:
        if key not in _PROJECT_FIELDS:
            violations.append(f"$.project: unexpected property {key!r}")
    title = project.get("title")
    if not isinstance(title, str) or not title.strip():
        violations.append("$.project.title: must be a non-empty string")
    elif len(title.strip()) > _MAX_TITLE_CHARS:
        violations.append(f"$.project.title: at most {_MAX_TITLE_CHARS} characters")
    goal = project.get("goal")
    if "goal" in project and goal is not None and not isinstance(goal, str):
        violations.append("$.project.goal: must be a string")
    for money_key in ("budget", "ai_budget"):
        if money_key in project and not _is_money(project[money_key]):
            violations.append(f"$.project.{money_key}: must be a number >= 0")
    deadline = project.get("deadline")
    if "deadline" in project and deadline is not None:
        if not isinstance(deadline, str):
            violations.append("$.project.deadline: must be a string")
        elif len(deadline) > _MAX_DEADLINE_CHARS:
            violations.append(f"$.project.deadline: at most {_MAX_DEADLINE_CHARS} characters")


def _validate_tasks(tasks: list, violations: list[str]) -> None:
    if not 1 <= len(tasks) <= MAX_PLAN_TASKS:
        violations.append(
            f"$.tasks: expected between 1 and {MAX_PLAN_TASKS} items, got {len(tasks)}"
        )
        return
    keys: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(tasks):
        path = f"$.tasks[{i}]"
        if not isinstance(item, dict):
            violations.append(f"{path}: expected an object")
            continue
        for key in item:
            if key not in _TASK_FIELDS:
                violations.append(f"{path}: unexpected property {key!r}")
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            violations.append(f"{path}.key: must be a non-empty string")
        elif key.strip() in seen:
            violations.append(f"{path}.key: duplicate key {key.strip()!r}")
        else:
            seen.add(key.strip())
            keys.append(key.strip())
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            violations.append(f"{path}.title: must be a non-empty string")
        elif len(title.strip()) > _MAX_TITLE_CHARS:
            violations.append(f"{path}.title: at most {_MAX_TITLE_CHARS} characters")
        owner = item.get("owner_agent_id")
        if not isinstance(owner, str) or not owner:
            violations.append(f"{path}.owner_agent_id: must be a non-empty string")
        summary = item.get("summary")
        if "summary" in item and summary is not None and not isinstance(summary, str):
            violations.append(f"{path}.summary: must be a string")
        if "eta_minutes" in item and not _is_count(item["eta_minutes"]):
            violations.append(f"{path}.eta_minutes: must be an integer >= 0")
        gate_at = item.get("gate_at", 100)
        if not _is_count(gate_at) or gate_at > 100:
            violations.append(f"{path}.gate_at: must be an integer 0..100")
        depends = item.get("depends_on", [])
        if not isinstance(depends, list):
            violations.append(f"{path}.depends_on: must be an array of keys")
        else:
            for j, dep in enumerate(depends):
                if not isinstance(dep, str) or not dep:
                    violations.append(f"{path}.depends_on[{j}]: must be a non-empty key")
        gate = item.get("gate")
        if "gate" in item and gate is not None:
            if not isinstance(gate, dict):
                violations.append(f"{path}.gate: must be an object")
            else:
                _validate_gate(path, gate, violations)
    if violations:
        return
    known = set(keys)
    for i, item in enumerate(tasks):
        assert isinstance(item, dict)
        key = item["key"].strip()
        for dep in item.get("depends_on", []):
            if dep not in known:
                violations.append(f"$.tasks[{i}].depends_on: unknown key {dep!r}")
            elif dep == key:
                violations.append(f"$.tasks[{i}].depends_on: self-dependency on {dep!r}")
    if violations:
        return
    cycle = _find_cycle(tasks)
    if cycle is not None:
        violations.append(f"$.tasks: dependency cycle: {' -> '.join(cycle)}")


def _validate_gate(path: str, gate: dict, violations: list[str]) -> None:
    for key in gate:
        if key not in _GATE_FIELDS:
            violations.append(f"{path}.gate: unexpected property {key!r}")
    title = gate.get("title")
    if not isinstance(title, str) or not title.strip():
        violations.append(f"{path}.gate.title: must be a non-empty string")
    elif len(title.strip()) > _MAX_TITLE_CHARS:
        violations.append(f"{path}.gate.title: at most {_MAX_TITLE_CHARS} characters")
    requester = gate.get("requested_by_agent_id")
    if not isinstance(requester, str) or not requester:
        violations.append(f"{path}.gate.requested_by_agent_id: must be a non-empty string")
    for field, limit in (("requested_by", _MAX_BY_CHARS), ("cost", _MAX_COST_CHARS)):
        value = gate.get(field)
        if field in gate and value is not None and not isinstance(value, str):
            violations.append(f"{path}.gate.{field}: must be a string")
        elif isinstance(value, str) and len(value) > limit:
            violations.append(f"{path}.gate.{field}: at most {limit} characters")
    for field in ("what", "expected_result", "affects", "rationale"):
        value = gate.get(field)
        if field in gate and value is not None and not isinstance(value, str):
            violations.append(f"{path}.gate.{field}: must be a string")


def _find_cycle(tasks: list[dict]) -> list[str] | None:
    """Depth-first cycle search over validated keys; None when acyclic."""
    edges = {t["key"].strip(): [d for d in t.get("depends_on", [])] for t in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    trail: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in visited:
            return None
        if node in visiting:
            loop = trail[trail.index(node) :] + [node]
            return loop
        visiting.add(node)
        trail.append(node)
        for dep in edges.get(node, []):
            hit = visit(dep)
            if hit is not None:
                return hit
        trail.pop()
        visiting.discard(node)
        visited.add(node)
        return None

    for key in edges:
        hit = visit(key)
        if hit is not None:
            return hit
    return None


def normalize_plan(data: dict) -> dict:
    """Pool of validated output only — call after validate_plan() == [].

    Builds allowlisted payloads: only columns the plan may set. Statuses,
    ids, progress, spend, and gate links are server-assigned, never taken
    from model output (rejected as violations above, never silently kept).
    """
    raw_project = data["project"]
    project: dict[str, Any] = {"title": raw_project["title"].strip()}
    if isinstance(raw_project.get("goal"), str):
        project["goal"] = raw_project["goal"]
    for money_key in ("budget", "ai_budget"):
        if money_key in raw_project:
            project[money_key] = raw_project[money_key]
    if isinstance(raw_project.get("deadline"), str):
        project["deadline"] = raw_project["deadline"]
    plan_tasks = []
    for item in data["tasks"]:
        task: dict[str, Any] = {
            "key": item["key"].strip(),
            "title": item["title"].strip(),
            "owner_agent_id": item["owner_agent_id"],
        }
        if isinstance(item.get("summary"), str):
            task["summary"] = item["summary"]
        if "eta_minutes" in item:
            task["eta_minutes"] = item["eta_minutes"]
        task["gate_at"] = item.get("gate_at", 100)
        task["depends_on"] = list(item.get("depends_on", []))
        gate = item.get("gate")
        if isinstance(gate, dict):
            task["gate"] = {
                "title": gate["title"].strip(),
                "requested_by_agent_id": gate["requested_by_agent_id"],
            }
            for field in (
                "requested_by",
                "what",
                "expected_result",
                "cost",
                "affects",
                "rationale",
            ):
                if isinstance(gate.get(field), str):
                    task["gate"][field] = gate[field]
        plan_tasks.append(task)
    return {"project": project, "tasks": plan_tasks}


async def generate_plan(
    provider: LlmProvider,
    settings: Settings,
    goal: str,
    summary: dict,
    roster: list[dict],
    *,
    max_generations: int = 2,
    meter: "TeamLeadMeter | None" = None,
) -> dict:
    """Ask the Team Lead for a work plan; raise on exhausted budget."""
    from app.teamlead.prompts import build_plan_system_prompt, build_plan_user_prompt

    last_violations: list[str] = ["no generation attempted"]
    for generation in range(max(1, max_generations)):
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=build_plan_system_prompt()),
                LlmMessage(
                    role="user",
                    content=build_plan_user_prompt(goal, summary, roster),
                ),
            ],
            model=getattr(provider, "_default_model", "mock"),
            max_tokens=None,
            temperature=None,
            structured=StructuredOutput(mode="json_schema", schema=dict(PLAN_SCHEMA)),
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
        last_violations = validate_plan(data)
        if not last_violations:
            return normalize_plan(data)
    raise BriefGenerationError(last_violations)
