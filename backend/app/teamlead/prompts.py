"""Prompt building for Team-Lead intake (Stage 8.1).

Pure functions (no DB, no network) — unit-testable in isolation.
Only non-secret business context goes into prompts (name/industry/goal);
API keys and session data never appear here. The questions schema is a
service constant: briefs carry no schema field, so the contract is untouched.
"""

from typing import Any

MIN_QUESTIONS = 2
MAX_QUESTIONS = 4
MAX_OPTIONS = 6
MAX_ANSWER_CHARS = 2000
MAX_SUMMARY_LIST_ITEMS = 20
MAX_PLAN_TASKS = 20

QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "text"],
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


def build_questions_system_prompt() -> str:
    return "\n".join(
        [
            "You are the Team Lead of an AI business team, running intake.",
            "Ask only what is truly needed to plan the work: 2 to 4 short,",
            "concrete clarifying questions. Rules:",
            "- Each question has a short stable id and one clear question text.",
            "- You may offer up to 6 short answer options per question, or none.",
            "- No flattery, no filler, no preamble outside the JSON object.",
            "- Answer with a single JSON object matching the requested schema.",
        ]
    )


def build_questions_user_prompt(business_name: str, industry: str, goal: str) -> str:
    parts = [f"Business: {business_name}"]
    if industry:
        parts.append(f"Industry: {industry}")
    parts.append(f"Owner goal: {goal}")
    parts.append("Produce 2 to 4 clarifying questions as JSON.")
    return "\n".join(parts)


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "goal",
        "context",
        "desired_outcome",
        "success_criteria",
        "assumptions",
        "open_questions",
    ],
    "properties": {
        "goal": {"type": "string"},
        "context": {"type": "array", "items": {"type": "string"}},
        "desired_outcome": {"type": "string"},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}


def build_summary_system_prompt() -> str:
    return "\n".join(
        [
            "You are the Team Lead of an AI business team, writing a structured",
            "brief from the owner's goal and their answers. Rules:",
            "- Restate the owner's goal faithfully; do not invent facts the goal",
            "  and answers do not support.",
            "- Derive context, constraints, and the desired outcome only from the",
            "  goal and the given answers.",
            "- State success criteria concretely enough to verify later.",
            "- Put anything uncertain, assumed, or still missing explicitly into",
            "  assumptions/open_questions — never present guesses as facts.",
            "- No flattery, no filler, no preamble outside the JSON object.",
            "- Answer with a single JSON object matching the requested schema.",
        ]
    )


def build_summary_user_prompt(goal: str, questions: list[dict], answers: list[dict]) -> str:
    by_id = {a["question_id"]: a["text"] for a in answers if isinstance(a, dict)}
    lines = [f"Owner goal: {goal}", "Questions and owner answers:"]
    for q in questions:
        qid = q.get("id", "?") if isinstance(q, dict) else "?"
        qtext = q.get("text", "") if isinstance(q, dict) else ""
        lines.append(f"- [{qid}] {qtext}")
        lines.append(f"  Answer: {by_id.get(qid, '')}")
    lines.append("Produce the structured brief as JSON.")
    return "\n".join(lines)


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["project", "tasks"],
    "properties": {
        "project": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "goal": {"type": "string"},
                "budget": {"type": "number"},
                "ai_budget": {"type": "number"},
                "deadline": {"type": "string"},
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key", "title", "owner_agent_id"],
                "properties": {
                    "key": {"type": "string"},
                    "title": {"type": "string"},
                    "owner_agent_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "eta_minutes": {"type": "integer"},
                    "gate_at": {"type": "integer"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "gate": {
                        "type": "object",
                        "required": ["title", "requested_by_agent_id"],
                        "properties": {
                            "title": {"type": "string"},
                            "requested_by_agent_id": {"type": "string"},
                            "requested_by": {"type": "string"},
                            "what": {"type": "string"},
                            "expected_result": {"type": "string"},
                            "cost": {"type": "string"},
                            "affects": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def build_plan_system_prompt() -> str:
    return "\n".join(
        [
            "You are the Team Lead of an AI business team, turning an approved",
            "brief into an executable work plan. Rules:",
            "- Use ONLY the agent ids from the roster below as owner_agent_id",
            "  / requested_by_agent_id; never invent agent ids.",
            "- At most 20 tasks; each task has a short stable key used only to",
            "  wire depends_on references within this plan.",
            "- depends_on may reference only keys defined in this plan, never",
            "  the task itself, and must not form a cycle.",
            "- A task needs a gate object only when a human must approve it;",
            "  otherwise omit gate entirely.",
            "- Task keys, statuses, ids, budgets-as-text, and progress are NOT",
            "  part of your output vocabulary: emit exactly the schema fields.",
            "- No flattery, no filler, no preamble outside the JSON object.",
            "- Answer with a single JSON object matching the requested schema.",
        ]
    )


def build_plan_user_prompt(goal: str, summary: dict, roster: list[dict]) -> str:
    import json as _json

    lines = [f"Owner goal: {goal}", f"Approved brief: {_json.dumps(summary, sort_keys=True)}"]
    lines.append("Agent roster (id — name, role):")
    for agent in roster:
        lines.append(f"- {agent.get('id')} — {agent.get('name')}, {agent.get('role')}")
    lines.append("Produce the project plan as JSON.")
    return "\n".join(lines)


def build_final_system_prompt() -> str:
    return "\n".join(
        [
            "You are the Team Lead of an AI business team, writing the final",
            "report for finished work. Rules:",
            "- Report ONLY the persisted facts given below: restate the goal",
            "  exactly, list completed work under completed_tasks and failed",
            "  work under failed_tasks with its persisted reason.",
            "- Never invent task ids, titles, outcomes, metrics, spend, or",
            "  approvals. Never claim every task succeeded: failed tasks stay",
            "  failed in your report.",
            "- Keep recommendations clearly separate from verified results.",
            "- Offer zero to five learnings: concise, reusable, grounded in",
            "  the results — never a copy of the report, never filler, never",
            "  secrets or credentials.",
            "- Say nothing about money beyond the given ledger figures: live",
            "  pricing is not tracked, so claim no measured costs.",
            "- No flattery, no filler, no preamble outside the JSON object.",
            "- Answer with a single JSON object matching the requested schema.",
        ]
    )


def build_final_user_prompt(facts: dict) -> str:
    import json as _json

    lines = [
        "Persisted project facts (the only truth — invent nothing):",
        _json.dumps(facts, sort_keys=True, default=str),
        "Produce the final report and learnings as JSON.",
    ]
    return "\n".join(lines)
