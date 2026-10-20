"""Brief finalization from owner answers (Stage 8.2).

Same discipline as question generation: one bounded LLM call per
generation, at most two generations per request, network never inside a DB
transaction. Validation is two layers: the shared structured-output path
(`validate_against_schema`) for shape, then explicit business rules the
validator subset cannot express (non-emptiness, list bounds — minLength-style
keywords are outside its subset and would be silently ignored).
"""

from typing import TYPE_CHECKING

from app.core.config import Settings
from app.llm.base import LlmProvider
from app.llm.types import BadResponseError, LlmMessage, LlmRequest, StructuredOutput
from app.llm.validate import validate_against_schema
from app.teamlead.prompts import MAX_ANSWER_CHARS, MAX_SUMMARY_LIST_ITEMS, SUMMARY_SCHEMA
from app.teamlead.questions import BriefGenerationError

if TYPE_CHECKING:
    from app.usage.metering import TeamLeadMeter

_SUMMARY_TEXT_FIELDS = ("goal", "desired_outcome")
_SUMMARY_LIST_FIELDS = ("context", "success_criteria", "assumptions", "open_questions")


def validate_answers(stored_questions: object, raw_answers: object) -> tuple[list[dict], list[str]]:
    """Check raw answers against the brief's stored questions.

    Returns (normalized, violations). Normalized answers follow stored
    question order, so idempotent repeats compare by plain equality.
    """
    violations: list[str] = []
    if not isinstance(stored_questions, list) or not stored_questions:
        return [], ["brief has no stored questions to answer"]
    known = [q.get("id") for q in stored_questions if isinstance(q, dict)]
    if not isinstance(raw_answers, list):
        return [], ["$.answers: expected an array"]
    seen: set[str] = set()
    by_id: dict[str, str] = {}
    for i, item in enumerate(raw_answers):
        path = f"$.answers[{i}]"
        if not isinstance(item, dict):
            violations.append(f"{path}: expected an object")
            continue
        qid = item.get("question_id")
        text = item.get("text")
        if not isinstance(qid, str) or not qid:
            violations.append(f"{path}.question_id: must be a non-empty string")
            continue
        if qid not in known:
            violations.append(f"{path}.question_id: unknown question {qid!r}")
            continue
        if qid in seen:
            violations.append(f"{path}.question_id: duplicate answer for {qid!r}")
            continue
        seen.add(qid)
        if not isinstance(text, str) or not text.strip():
            violations.append(f"{path}.text: must be a non-empty string")
            continue
        if len(text.strip()) > MAX_ANSWER_CHARS:
            violations.append(
                f"{path}.text: at most {MAX_ANSWER_CHARS} characters, got {len(text.strip())}"
            )
            continue
        by_id[qid] = text.strip()
    if violations:
        return [], violations
    missing = [qid for qid in known if qid not in by_id]
    if missing:
        return [], [f"$.answers: missing answers for {missing!r}"]
    normalized = [{"question_id": qid, "text": by_id[qid]} for qid in known]
    return normalized, []


def validate_summary(data: object) -> list[str]:
    """Collect every violation (never fail-fast, never coerce, never invent)."""
    violations = validate_against_schema(data, SUMMARY_SCHEMA)
    if violations:
        return violations
    assert isinstance(data, dict)
    for field in _SUMMARY_TEXT_FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append(f"$.{field}: must be a non-empty string")
    for field in _SUMMARY_LIST_FIELDS:
        value = data.get(field)
        if not isinstance(value, list):
            violations.append(f"$.{field}: must be an array of strings")
            continue
        if len(value) > MAX_SUMMARY_LIST_ITEMS:
            violations.append(
                f"$.{field}: at most {MAX_SUMMARY_LIST_ITEMS} items, got {len(value)}"
            )
        for j, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                violations.append(f"$.{field}[{j}]: must be a non-empty string")
    return violations


def normalize_summary(data: dict) -> dict:
    """Pool of validated output only — call after validate_summary() == []."""
    normalized = {
        "goal": data["goal"].strip(),
        "desired_outcome": data["desired_outcome"].strip(),
    }
    for field in _SUMMARY_LIST_FIELDS:
        normalized[field] = [item.strip() for item in data[field]]
    return normalized


async def generate_summary(
    provider: LlmProvider,
    settings: Settings,
    goal: str,
    questions: list[dict],
    answers: list[dict],
    *,
    max_generations: int = 2,
    meter: "TeamLeadMeter | None" = None,
) -> dict:
    """Ask the Team Lead for a structured brief; raise on exhausted budget."""
    from app.teamlead.prompts import build_summary_system_prompt, build_summary_user_prompt

    last_violations: list[str] = ["no generation attempted"]
    for generation in range(max(1, max_generations)):
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=build_summary_system_prompt()),
                LlmMessage(
                    role="user",
                    content=build_summary_user_prompt(goal, questions, answers),
                ),
            ],
            model=getattr(provider, "_default_model", "mock"),
            max_tokens=None,
            temperature=None,
            structured=StructuredOutput(mode="json_schema", schema=dict(SUMMARY_SCHEMA)),
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
        last_violations = validate_summary(data)
        if not last_violations:
            return normalize_summary(data)
    raise BriefGenerationError(last_violations)
