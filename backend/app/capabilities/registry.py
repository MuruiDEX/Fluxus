"""Capability registry + deterministic grant resolution.

Capabilities are opt-in through the agent's EXISTING profile columns:
`tools` and `permissions`. Free-form profile strings resolve through a fixed
alias table; canonical capability ids always work. `permissions.denied` wins
over every grant. There are no wildcard grants and no implicit defaults: an
agent with an empty profile keeps the legacy single-call execution path.

Aliases are compared after normalization (lowercase, non-alphanumerics to a
single space, trimmed) and must match the whole string. "Assign tasks" grants
delegation; the unrelated word "tasks" inside a sentence never does.
"""

import re
from typing import Any

from app.capabilities.types import ApprovalPolicy, CapabilityRisk, CapabilitySpec
from app.persistence.enums import BrainCategory, TaskStatus


def normalize_grant(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        id="business.context",
        title="Business context",
        description=(
            "Read the current business profile, project goal and a bounded "
            "summary of persistent company knowledge."
        ),
        risk=CapabilityRisk.READ,
        aliases=("business context", "company context", "company memory"),
        parameters=_object({}),
    ),
    CapabilitySpec(
        id="project.tasks",
        title="Project tasks",
        description=(
            "List the tasks of the current project with status, owner, "
            "progress and approval gate. Read-only."
        ),
        risk=CapabilityRisk.READ,
        aliases=("project tasks", "tasks", "task board", "planner", "plans"),
        parameters=_object(
            {
                "status": {
                    "type": "string",
                    "enum": [status.value for status in TaskStatus],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }
        ),
    ),
    CapabilitySpec(
        id="task.result",
        title="Task result",
        description=(
            "Read the persisted result summary of a task in this project. "
            "Returns only rows the business can already read."
        ),
        risk=CapabilityRisk.READ,
        aliases=("task result", "task results", "results", "completed results"),
        parameters=_object({"task_id": {"type": "string"}}, required=["task_id"]),
    ),
    CapabilitySpec(
        id="knowledge.search",
        title="Knowledge search",
        description=(
            "Search the business knowledge base (company memory) for entries "
            "matching a query. Read-only and bounded."
        ),
        risk=CapabilityRisk.READ,
        aliases=(
            "knowledge search",
            "knowledge",
            "web research",
            "research",
            "brain search",
            "business brain",
        ),
        parameters=_object(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "category": {
                    "type": "string",
                    "enum": [category.value for category in BrainCategory],
                },
            },
            required=["query"],
        ),
    ),
    CapabilitySpec(
        id="approvals.request",
        title="Request approval",
        description=(
            "Create a REAL pending human approval for a proposed action. "
            "Never approves anything by itself; the owner decides."
        ),
        risk=CapabilityRisk.WRITE,
        approval=ApprovalPolicy.NONE,
        aliases=("approvals", "request approval", "approval request"),
        parameters=_object(
            {
                "title": {"type": "string"},
                "what": {"type": "string"},
                "expected_result": {"type": "string"},
                "cost": {"type": "string"},
                "affects": {"type": "string"},
                "rationale": {"type": "string"},
            },
            required=["title"],
        ),
    ),
    CapabilitySpec(
        id="task.delegate",
        title="Delegate a task",
        description=(
            "Create a REAL child task owned by another hired agent, gated by "
            "a new pending human approval. The child cannot run until the "
            "approval is granted."
        ),
        risk=CapabilityRisk.WRITE,
        approval=ApprovalPolicy.NONE,
        aliases=("delegate", "delegation", "assign tasks", "assign task", "assign"),
        parameters=_object(
            {
                "title": {"type": "string"},
                "owner_agent_id": {"type": "string"},
                "notes": {"type": "string"},
                "eta_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            },
            required=["title", "owner_agent_id"],
        ),
    ),
)

_BY_ID: dict[str, CapabilitySpec] = {spec.id: spec for spec in CAPABILITIES}
_ALIAS_TO_IDS: dict[str, tuple[str, ...]] = {}
# Provider-contributed capabilities register here at composition time
# (`app.integrations.bootstrap`). Core capabilities stay static and ordered.
_EXTRA_SPECS: list[CapabilitySpec] = []
_CORE_CAPABILITIES: tuple[CapabilitySpec, ...] = CAPABILITIES


def _rebuild_indexes() -> None:
    global CAPABILITIES
    CAPABILITIES = (*_CORE_CAPABILITIES, *_EXTRA_SPECS)
    _BY_ID.clear()
    _ALIAS_TO_IDS.clear()
    for spec in CAPABILITIES:
        _BY_ID[spec.id] = spec
        alias_keys = [normalize_grant(alias) for alias in spec.aliases]
        alias_keys.append(normalize_grant(spec.id))
        for key in alias_keys:
            if not key:
                continue
            existing = _ALIAS_TO_IDS.get(key, ())
            if spec.id not in existing:
                _ALIAS_TO_IDS[key] = (*existing, spec.id)


for _spec in CAPABILITIES:
    _alias_keys = [normalize_grant(alias) for alias in _spec.aliases]
    _alias_keys.append(normalize_grant(_spec.id))
    for _key in _alias_keys:
        if not _key:
            continue
        _existing = _ALIAS_TO_IDS.get(_key, ())
        if _spec.id not in _existing:
            _ALIAS_TO_IDS[_key] = (*_existing, _spec.id)


def register_capabilities(specs: list[CapabilitySpec] | tuple[CapabilitySpec, ...]) -> None:
    """Add provider capabilities without touching the core registry.

    Idempotent by canonical id. Registration is composition-time only (module
    import); execution never mutates the registry.
    """
    changed = False
    for spec in specs:
        if any(existing.id == spec.id for existing in _EXTRA_SPECS):
            continue
        if spec.id in _BY_ID:
            raise ValueError(f"capability id already registered: {spec.id}")
        _EXTRA_SPECS.append(spec)
        changed = True
    if changed:
        _rebuild_indexes()


def get_spec(capability_id: str) -> CapabilitySpec | None:
    """Resolve a model-requested id. Only canonical ids are callable."""
    return _BY_ID.get(capability_id)


def resolve_grants(tools: Any, permissions: Any) -> list[CapabilitySpec]:
    """Resolve an agent profile to the capabilities it may use.

    `permissions` values are inspected for every key except `denied`; values
    may be lists or plain strings. Missing/malformed fields grant nothing.
    """
    sources: list[str] = []
    if isinstance(tools, list | tuple):
        sources.extend(item for item in tools if isinstance(item, str))
    denied: list[str] = []
    if isinstance(permissions, dict):
        for key, value in permissions.items():
            values = value if isinstance(value, list | tuple) else [value]
            if key == "denied":
                denied.extend(item for item in values if isinstance(item, str))
            else:
                sources.extend(item for item in values if isinstance(item, str))

    granted: set[str] = set()
    for source in sources:
        key = normalize_grant(source)
        if key:
            granted.update(_ALIAS_TO_IDS.get(key, ()))
    for source in denied:
        key = normalize_grant(source)
        if key:
            granted.difference_update(_ALIAS_TO_IDS.get(key, ()))
    return [spec for spec in CAPABILITIES if spec.id in granted]


__all__ = [
    "CAPABILITIES",
    "get_spec",
    "normalize_grant",
    "register_capabilities",
    "resolve_grants",
]
