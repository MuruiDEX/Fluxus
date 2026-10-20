"""Executor unit tests — no database: prompts, envelopes, transitions, mapping."""

import json

from app.execution import prompts
from app.execution.executor import (
    _CLAIMABLE,
    MAX_ATTEMPTS,
    ExecutionRequest,
)
from app.llm.types import (
    LlmConfigurationError,
    ProviderAuthError,
)
from app.persistence.enums import TaskStatus


def test_result_schema_shape() -> None:
    assert prompts.RESULT_SCHEMA["type"] == "object"
    assert "summary" in prompts.RESULT_SCHEMA["required"]


def test_prompts_contain_scope_and_boundaries() -> None:
    system = prompts.build_system_prompt("Analyst", ["research", "summarize"])
    assert "Analyst" in system and "research" in system
    assert "Never claim" in system and "Never change statuses" in system
    user = prompts.build_user_prompt("Acme", "Launch", "Grow fast", "Research", "notes here")
    for token in ("Acme", "Launch", "Grow fast", "Research", "notes here"):
        assert token in user


def test_envelope_fits_without_breaking_structure() -> None:
    big_findings = [f"finding-{i}-" + "x" * 500 for i in range(100)]
    envelope = prompts.build_envelope(
        task_id="t_1",
        model="m",
        summary="s" * 5000,
        output={"summary": "s", "findings": big_findings},
        usage={"prompt_tokens": 1},
        attempts=1,
    )
    fitted = prompts.fit_envelope(envelope)
    text = json.dumps(fitted, ensure_ascii=False)
    assert len(text) <= prompts.MAX_ENVELOPE_CHARS
    # Structure intact: valid JSON, findings are whole strings.
    parsed = json.loads(text)
    assert parsed["type"] == "task_llm_result"
    assert all(isinstance(f, str) for f in parsed["output"]["findings"])
    assert len(parsed["summary"]) <= prompts.MAX_SUMMARY_CHARS


def test_envelope_passthrough_for_small_results() -> None:
    envelope = prompts.build_envelope(
        task_id="t_1",
        model="m",
        summary="done",
        output={"summary": "done"},
        usage={},
        attempts=1,
    )
    assert prompts.fit_envelope(envelope) == envelope


def test_rerun_policy_classes() -> None:
    # Configuration errors must fail before any claim; auth errors are
    # re-runnable within the attempts budget (key may be fixed by ops).
    assert issubclass(LlmConfigurationError, Exception)
    assert not issubclass(ProviderAuthError, LlmConfigurationError)
    assert MAX_ATTEMPTS == 3


def test_claimable_statuses_match_contract() -> None:
    assert set(_CLAIMABLE) == {"not_started", "failed"}
    assert TaskStatus.COMPLETED.value not in _CLAIMABLE
    assert TaskStatus.WORKING.value not in _CLAIMABLE


def test_request_shape() -> None:
    req = ExecutionRequest(business_id="b", project_id="p", task_id="t")
    assert req.timeout_s is None
