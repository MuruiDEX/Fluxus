"""Capability Engine unit tests — no database, no network.

Covers the security-critical pure logic: grant resolution, request parsing,
usage aggregation and envelope fitting. DB-backed behavior lives in
`test_capabilities_integration.py`.
"""

import json

from app.capabilities.engine import aggregate_usage
from app.capabilities.protocol import (
    build_capability_block,
    format_tool_results,
    parse_tool_requests,
)
from app.capabilities.registry import CAPABILITIES, get_spec, resolve_grants
from app.capabilities.types import CapabilityResult, CapabilityRisk
from app.core.config import Settings
from app.execution import prompts
from app.llm.types import LlmUsage


def test_empty_profile_grants_nothing() -> None:
    assert resolve_grants([], {}) == []
    assert resolve_grants(None, None) == []
    assert resolve_grants(["Marketplace", "Sheets"], {"denied": []}) == []


def test_canonical_ids_and_aliases_grant_deterministically() -> None:
    ids = {spec.id for spec in resolve_grants(["project.tasks", "Planner"], {})}
    assert ids == {"project.tasks"}

    # Case, punctuation and spacing are normalized; the whole string must
    # match an alias, so a sentence containing "tasks" grants nothing.
    assert {s.id for s in resolve_grants([" Web  RESEARCH "], {})} == {"knowledge.search"}
    assert {s.id for s in resolve_grants([], {"write": ["Assign Tasks"]})} == {"task.delegate"}
    assert resolve_grants(["Assign tasks to anyone without approval"], {}) == []


def test_explicit_denied_revokes_even_an_explicit_grant() -> None:
    perms = {
        "read": ["Business Brain"],
        "actions": ["delegate"],
        "denied": ["Delegate", "knowledge search"],
    }
    assert resolve_grants(["task.delegate", "knowledge.search"], perms) == []


def test_malformed_profiles_grant_nothing() -> None:
    assert resolve_grants("business.context", {"read": 3}) == []
    assert resolve_grants([1, None, "business.context"], "nope") == [
        spec for spec in CAPABILITIES if spec.id == "business.context"
    ]


def test_registry_alias_table_is_unambiguous_and_explicit() -> None:
    assert get_spec("business.context") is not None
    assert get_spec("Business Context") is None  # only canonical ids are callable
    assert all(spec.risk in (CapabilityRisk.READ, CapabilityRisk.WRITE) for spec in CAPABILITIES)


def test_parse_tool_requests_accepts_valid_and_bounds_extras() -> None:
    data = {
        "summary": "need data",
        "tool_requests": [
            {"capability": "business.context", "arguments": {}, "request_id": "r1"},
            {"capability": "project.tasks", "arguments": {"limit": 5}},
            {"capability": "project.tasks", "arguments": {"limit": 6}},
        ],
    }
    calls, problems = parse_tool_requests(data, max_requests=2)
    assert [call.capability for call in calls] == ["business.context", "project.tasks"]
    assert calls[0].request_id == "r1"
    assert len(problems) == 1 and "at most 2" in problems[0]


def test_parse_tool_requests_rejects_unknown_and_oversized() -> None:
    data = {
        "summary": "x",
        "tool_requests": [
            {"capability": "shell.exec", "arguments": {}},
            {"capability": "business.context", "arguments": {"x": "y" * 5000}},
            {"capability": "business.context", "arguments": "not-an-object"},
            "not-a-request",
            {"arguments": {}},
        ],
    }
    calls, problems = parse_tool_requests(data, max_requests=8)
    assert calls == []
    assert len(problems) == 5
    assert any("unknown capability" in item for item in problems)
    assert any("exceed" in item for item in problems)
    assert any("arguments must be an object" in item for item in problems)


def test_parse_tool_requests_ignores_absent_or_null() -> None:
    assert parse_tool_requests({"summary": "ok"}, max_requests=3) == ([], [])
    assert parse_tool_requests({"summary": "ok", "tool_requests": None}, max_requests=3) == ([], [])
    calls, problems = parse_tool_requests(
        {"summary": "x", "tool_requests": {"capability": "business.context"}},
        max_requests=3,
    )
    assert calls == [] and problems


def test_capability_block_lists_only_granted_ids_and_limits() -> None:
    specs = resolve_grants(["Planner", "delegate"], {})
    block = build_capability_block(specs, max_llm_calls=3)
    assert "project.tasks" in block and "task.delegate" in block
    assert "knowledge.search" not in block
    assert "at most 3 model turns" in block
    assert "REAL pending records" in block
    assert "owner_agent_id" in block  # argument schema is exposed to the model
    assert "limit" in block


def test_aggregate_usage_sums_and_only_claims_reported_when_all_reported() -> None:
    reported = LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, reported=True)
    missing = LlmUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5, reported=False)
    total = aggregate_usage([reported, missing])
    assert (total.prompt_tokens, total.completion_tokens, total.total_tokens) == (13, 7, 20)
    assert total.reported is False
    assert aggregate_usage([reported, reported]).reported is True


def test_tool_results_are_bounded_and_never_break_json() -> None:
    results = [
        CapabilityResult(
            capability="knowledge.search",
            ok=True,
            data={"body": "x" * 5000},
        )
    ]
    text = format_tool_results(results, char_cap=500)
    assert len(text) <= 500
    parsed = json.loads(text)
    assert "toolResults" in parsed


def test_multiple_tool_results_keep_every_status_and_a_preview() -> None:
    results = [
        CapabilityResult(capability=f"cap.{index}", ok=True, data={"body": "x" * 5000})
        for index in range(4)
    ]
    text = format_tool_results(results, char_cap=1200)
    assert len(text) <= 1200
    parsed = json.loads(text)["toolResults"]
    assert [item["capability"] for item in parsed] == [
        "cap.0",
        "cap.1",
        "cap.2",
        "cap.3",
    ]
    assert all(item["ok"] is True and item["data"]["truncated"] for item in parsed)


def test_envelope_fits_capability_trace_and_keeps_structure() -> None:
    trace = [
        {"capability": "project.tasks", "status": "ok", "error": None}
        for _ in range(prompts.MAX_CAPABILITY_TRACE + 10)
    ]
    envelope = prompts.fit_envelope(
        prompts.build_envelope(
            task_id="t_1",
            model="mock",
            summary="done",
            output={"summary": "done"},
            usage={},
            attempts=2,
            capabilities={"enabled": True, "llmCalls": 2, "calls": trace},
        )
    )
    assert len(json.dumps(envelope)) <= prompts.MAX_ENVELOPE_CHARS
    assert len(envelope["capabilities"]["calls"]) == prompts.MAX_CAPABILITY_TRACE
    assert envelope["capabilities"]["truncated"] is True


def test_envelope_drops_call_detail_before_raising() -> None:
    trace = [
        {"capability": "c" * 120, "status": "error", "error": "e" * 120}
        for _ in range(prompts.MAX_CAPABILITY_TRACE)
    ]
    envelope = prompts.fit_envelope(
        prompts.build_envelope(
            task_id="t_1",
            model="mock",
            summary="s",
            output={"summary": "s"},
            usage={},
            attempts=1,
            capabilities={"enabled": True, "llmCalls": 4, "calls": trace},
        )
    )
    assert envelope["capabilities"]["calls"] == []
    assert envelope["capabilities"]["truncated"] is True


def test_result_schema_is_unchanged_without_capabilities() -> None:
    legacy = prompts.result_schema(capabilities=False)
    assert legacy == prompts.RESULT_SCHEMA
    assert "tool_requests" not in legacy["properties"]

    enabled = prompts.result_schema(capabilities=True)
    assert "tool_requests" in enabled["properties"]
    assert "tool_requests" not in legacy["properties"]


def test_capability_settings_are_bounded() -> None:
    restricted = Settings(execution_max_llm_calls=1, execution_max_write_calls_per_run=0)
    assert restricted.execution_max_llm_calls == 1
    for bad in (
        {"execution_max_llm_calls": 0},
        {"execution_max_llm_calls": 9},
        {"execution_max_tool_requests_per_step": 0},
        {"execution_max_write_calls_per_run": 11},
        {"execution_capability_result_chars": 100},
        {"execution_capability_timeout_s": 0},
    ):
        try:
            Settings(**bad)
        except ValueError:
            continue
        raise AssertionError(f"expected Settings to reject {bad}")
