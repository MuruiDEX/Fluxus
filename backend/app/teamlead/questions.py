"""Clarifying-question generation (Stage 8.1).

One bounded LLM call per generation, at most two generations per request.
The network call never runs inside a DB transaction — callers generate
first, then persist. Validation is two layers, mirroring the execution
engine: the shared structured-output path (`validate_against_schema`) for
shape, then explicit business rules the validator subset cannot express
(count bounds, non-emptiness, uniqueness — minLength-style keywords are
outside its subset and would be silently ignored).
"""

from typing import TYPE_CHECKING

from app.core.config import Settings
from app.llm.base import LlmProvider
from app.llm.types import BadResponseError, LlmMessage, LlmRequest, StructuredOutput
from app.llm.validate import validate_against_schema
from app.teamlead.prompts import MAX_OPTIONS, MAX_QUESTIONS, MIN_QUESTIONS, QUESTIONS_SCHEMA

if TYPE_CHECKING:
    from app.usage.metering import TeamLeadMeter


class BriefGenerationError(Exception):
    """Model output stayed unusable after the generation budget.

    Carries safe, displayable violation strings (no prompts, no secrets).
    """

    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


def validate_questions(data: object) -> list[str]:
    """Collect every violation (never fail-fast, never coerce, never invent)."""
    violations = validate_against_schema(data, QUESTIONS_SCHEMA)
    if violations:
        return violations
    assert isinstance(data, dict)
    questions = data["questions"]
    assert isinstance(questions, list)
    if not MIN_QUESTIONS <= len(questions) <= MAX_QUESTIONS:
        violations.append(
            f"$.questions: expected between {MIN_QUESTIONS} and {MAX_QUESTIONS} items, "
            f"got {len(questions)}"
        )
        return violations
    seen: set[str] = set()
    for i, item in enumerate(questions):
        path = f"$.questions[{i}]"
        if not isinstance(item, dict):
            violations.append(f"{path}: expected an object")
            continue
        qid = item.get("id")
        text = item.get("text")
        if not isinstance(qid, str) or not qid.strip():
            violations.append(f"{path}.id: must be a non-empty string")
        elif qid.strip() in seen:
            violations.append(f"{path}.id: duplicate id {qid.strip()!r}")
        else:
            seen.add(qid.strip())
        if not isinstance(text, str) or not text.strip():
            violations.append(f"{path}.text: must be a non-empty string")
        if "options" in item and item["options"] is not None:
            options = item["options"]
            if not isinstance(options, list):
                violations.append(f"{path}.options: must be an array of strings")
            else:
                if len(options) > MAX_OPTIONS:
                    violations.append(
                        f"{path}.options: at most {MAX_OPTIONS} options, got {len(options)}"
                    )
                for j, option in enumerate(options):
                    if not isinstance(option, str) or not option.strip():
                        violations.append(f"{path}.options[{j}]: must be a non-empty string")
    return violations


def normalize_questions(data: dict) -> list[dict]:
    """Pool of validated output only — call after validate_questions() == []."""
    normalized = []
    for item in data["questions"]:
        question = {"id": item["id"].strip(), "text": item["text"].strip()}
        if isinstance(item.get("options"), list) and item["options"]:
            question["options"] = [o.strip() for o in item["options"]]
        normalized.append(question)
    return normalized


async def generate_questions(
    provider: LlmProvider,
    settings: Settings,
    business_name: str,
    industry: str,
    goal: str,
    *,
    max_generations: int = 2,
    meter: "TeamLeadMeter | None" = None,
) -> list[dict]:
    """Ask the Team Lead for clarifying questions; raise on exhausted budget."""
    from app.teamlead.prompts import build_questions_system_prompt, build_questions_user_prompt

    last_violations: list[str] = ["no generation attempted"]
    for generation in range(max(1, max_generations)):
        request = LlmRequest(
            messages=[
                LlmMessage(role="system", content=build_questions_system_prompt()),
                LlmMessage(
                    role="user",
                    content=build_questions_user_prompt(business_name, industry, goal),
                ),
            ],
            model=getattr(provider, "_default_model", "mock"),
            # An explicit output cap: it is sent to the provider AND is the
            # bound the budget reservation is priced from (Stage 18 S1).
            max_tokens=settings.team_lead_max_tokens,
            temperature=None,
            structured=StructuredOutput(mode="json_schema", schema=dict(QUESTIONS_SCHEMA)),
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
        last_violations = validate_questions(data)
        if not last_violations:
            return normalize_questions(data)
    raise BriefGenerationError(last_violations)
