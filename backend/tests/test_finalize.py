"""Atomic brief finalization: report + learnings + completed (Stage 8.5).

POST .../briefs/{id}/finalize runs one bounded Team-Lead generation (at most
two LLM calls) OUTSIDE any database transaction, then persists the validated
report envelope + Brain learnings + the planned → completed transition + the
audit event in ONE transaction. Eligibility is revalidated inside the final
transaction, so races with execution/approval changes cannot finalize stale
state. Concurrent finalizers serialize: exactly one batch wins.
"""

import asyncio
import json as _json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.mock import MockProvider
from app.llm.types import ProviderError
from app.persistence.repositories.concrete import (
    ActivityRepository,
    ApprovalRepository,
    BrainRepository,
    BriefRepository,
    TaskRepository,
)
from app.teamlead.finalize import (
    generate_finalization,
    normalize_finalization,
    validate_finalization,
)
from app.teamlead.prompts import build_final_user_prompt
from app.teamlead.questions import BriefGenerationError
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_business, make_world
from tests.test_briefs import VALID_TEXT, _authed, _brief_url, _mocked
from tests.test_planning import PLAN_TEXT, _make_plannable, _plan_url

_tasks = TaskRepository()
_approvals = ApprovalRepository()
_brains = BrainRepository()
_briefs = BriefRepository()
_activity = ActivityRepository()

GOAL = "Grow fast"


def _final_url(brief_id: str, biz: str = BIZ) -> str:
    return f"{_brief_url(biz)}/{brief_id}/finalize"


def _final_text(
    tasks: list[dict],
    *,
    failed_ids: set[str] | None = None,
    learnings: list[dict] | None = None,
) -> str:
    failed_ids = failed_ids or set()
    completed = [
        {"id": t["id"], "title": t["title"], "summary": f"Done: {t['title']}"}
        for t in tasks
        if t["id"] not in failed_ids
    ]
    failed = [
        {"id": t["id"], "title": t["title"], "reason": "Ran out of attempts"}
        for t in tasks
        if t["id"] in failed_ids
    ]
    if learnings is None:
        learnings = [
            {
                "category": "results",
                "title": "Small slices ship",
                "body": "Shipping in small slices worked well here.",
            }
        ]
    return _json.dumps(
        {
            "report": {
                "goal": GOAL,
                "completed_tasks": completed,
                "failed_tasks": failed,
                "limitations": ["Limited sample"],
                "recommendations": ["Keep going"],
            },
            "learnings": learnings,
        }
    )


def _facts(tasks: list[dict]) -> dict:
    return {
        "goal": GOAL,
        "tasks": [
            {"id": t["id"], "title": t["title"], "status": "completed", "summary": "x"}
            for t in tasks
        ],
        "approvals": [],
        "ledger": {"budget": "1000", "ai_budget": "100", "ai_spent": "0"},
    }


async def _make_planned(
    api_client: httpx.AsyncClient,
    headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str, list[dict], list[dict]]:
    """Drive a brief to planned through the real endpoints; return ids + graph."""
    brief_id, _ = await _make_plannable(api_client, headers, monkeypatch, plan_texts=[PLAN_TEXT])
    planned = await api_client.post(_plan_url(brief_id), headers=headers)
    assert planned.status_code == 200, planned.text
    body = planned.json()
    return brief_id, body["project"]["id"], body["tasks"], body["approvals"]


async def _settle(
    session: AsyncSession,
    tasks: list[dict],
    approvals: list[dict],
    *,
    failed_ids: set[str] | None = None,
) -> None:
    """Simulate executed work: terminal task + approval states, committed."""
    failed_ids = failed_ids or set()
    for task in tasks:
        status = "failed" if task["id"] in failed_ids else "completed"
        updated = await _tasks.update(session, BIZ, task["id"], {"status": status})
        assert updated is not None
    for approval in approvals:
        assert await _approvals.decide(session, BIZ, approval["id"], to_status="approved")
    await session.commit()


async def _counts(session: AsyncSession, project_id: str) -> tuple[int, int]:
    brains = await _brains.count(session, BIZ)
    events = await _activity.count(session, BIZ, project_id=project_id)
    return brains, events


async def _brief_row(session: AsyncSession, brief_id: str):
    row = await _briefs.get(session, BIZ, brief_id)
    assert row is not None
    return row


def test_final_prompt_contains_facts_not_secrets() -> None:
    text = build_final_user_prompt(_facts([{"id": "t_1", "title": "T"}]))
    assert "t_1" in text and GOAL in text


def test_validate_finalization_accepts_valid() -> None:
    tasks = [{"id": "t_1", "title": "T"}]
    data = _json.loads(_final_text(tasks))
    assert validate_finalization(data, _facts(tasks)) == []


def test_validate_finalization_rejects() -> None:
    tasks = [{"id": "t_1", "title": "T"}]
    assert validate_finalization({}, _facts(tasks)) != []
    invented = _json.loads(_final_text([{"id": "ghost", "title": "G"}]))
    assert validate_finalization(invented, _facts(tasks)) != []
    misattributed = _json.loads(_final_text(tasks))
    misattributed["report"]["failed_tasks"] = misattributed["report"].pop("completed_tasks")
    assert validate_finalization(misattributed, _facts(tasks)) != []
    wrong_goal = _json.loads(_final_text(tasks))
    wrong_goal["report"]["goal"] = "Something else"
    assert validate_finalization(wrong_goal, _facts(tasks)) != []
    renamed = _json.loads(_final_text(tasks))
    renamed["report"]["completed_tasks"][0]["title"] = "Other"
    assert validate_finalization(renamed, _facts(tasks)) != []
    smuggled = _json.loads(_final_text(tasks))
    smuggled["report"]["budget"] = 5
    assert validate_finalization(smuggled, _facts(tasks)) != []
    many = _json.loads(_final_text(tasks))
    many["learnings"] = [{"category": "results", "title": f"L{i}", "body": "b"} for i in range(6)]
    assert validate_finalization(many, _facts(tasks)) != []
    bad_cat = _json.loads(_final_text(tasks))
    bad_cat["learnings"][0]["category"] = "vibes"
    assert validate_finalization(bad_cat, _facts(tasks)) != []
    secret = _json.loads(_final_text(tasks))
    secret["learnings"][0]["body"] = "The api_key worked well here."
    assert validate_finalization(secret, _facts(tasks)) != []
    copy = _json.loads(_final_text(tasks))
    copy["learnings"][0]["body"] = "x"
    assert validate_finalization(copy, _facts(tasks)) != []


def test_normalize_finalization_strips() -> None:
    data = _json.loads(_final_text([{"id": "t_1", "title": "T"}]))
    data["report"]["limitations"] = ["  a  "]
    out = normalize_finalization(data)
    assert out["report"]["limitations"] == ["a"]
    assert out["learnings"][0]["title"] == "Small slices ship"


async def test_generate_finalization_success_single_call() -> None:
    tasks = [{"id": "t_1", "title": "T"}]
    provider = MockProvider(texts=[_final_text(tasks)])
    out = await generate_finalization(provider, Settings(), _facts(tasks))
    assert out["report"]["goal"] == GOAL
    assert len(provider.calls) == 1


async def test_generate_finalization_retries_then_succeeds() -> None:
    tasks = [{"id": "t_1", "title": "T"}]
    provider = MockProvider(texts=['{"bogus": 1}', _final_text(tasks)])
    out = await generate_finalization(provider, Settings(), _facts(tasks))
    assert out["report"]["completed_tasks"][0]["id"] == "t_1"
    assert len(provider.calls) == 2


async def test_generate_finalization_exhaustion_raises() -> None:
    tasks = [{"id": "t_1", "title": "T"}]
    provider = MockProvider(texts=['{"bogus": 1}', '{"bogus": 2}'])
    with pytest.raises(BriefGenerationError):
        await generate_finalization(provider, Settings(), _facts(tasks))
    assert len(provider.calls) == 2


async def test_generate_finalization_propagates_cancel() -> None:
    tasks = [{"id": "t_1", "title": "T"}]
    provider = MockProvider(texts=[_final_text(tasks)], latency_s=30.0)
    task = asyncio.ensure_future(generate_finalization(provider, Settings(), _facts(tasks)))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_finalize_ok(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    provider = MockProvider(texts=[_final_text(tasks)])
    _mocked(monkeypatch, provider)

    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["briefId"] == brief_id and body["projectId"] == project_id
    assert body["status"] == "completed"
    assert body["report"]["goal"] == GOAL
    assert {t["id"] for t in body["report"]["completed_tasks"]} == {t["id"] for t in tasks}
    assert body["report"]["failed_tasks"] == []
    assert len(body["learnings"]) == 1
    assert body["learnings"][0]["category"] == "results"
    assert body["costs"]["note"].startswith("Configured ledger values")
    assert body["costs"]["ai_spent"] == "0.00"
    assert len(provider.calls) == 1

    brains, events = await _counts(session, project_id)
    assert brains == 1 and events == 1

    # Persisted: fresh GET shows completed + the stored envelope.
    brief = await api_client.get(f"{_brief_url()}/{brief_id}", headers=headers)
    assert brief.json()["status"] == "completed"
    assert brief.json()["finalReport"]["report"] == body["report"]
    row = await _brief_row(session, brief_id)
    assert row.status == "completed" and row.project_id == project_id


async def test_finalize_zero_and_five_learnings(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)

    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks, learnings=[])]))
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["learnings"] == []
    brains, _ = await _counts(session, project_id)
    assert brains == 0

    brief_id, _, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    five = [
        {"category": "results", "title": f"L{i}", "body": f"Learning {i} from this run."}
        for i in range(5)
    ]
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks, learnings=five)]))
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 200, res.text
    assert len(res.json()["learnings"]) == 5


async def test_finalize_six_learnings_rejected(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    six = [{"category": "results", "title": f"L{i}", "body": f"Learning {i}."} for i in range(6)]
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks, learnings=six)]))
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 422, res.text
    brains, events = await _counts(session, project_id)
    assert brains == 0 and events == 0
    row = await _brief_row(session, brief_id)
    assert row.status == "planned" and row.final_report is None


async def test_finalize_exhaustion(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    provider = MockProvider(texts=["not json", '{"bogus": 1}'])
    _mocked(monkeypatch, provider)
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 422, res.text
    assert len(provider.calls) == 2
    brains, events = await _counts(session, project_id)
    assert brains == 0 and events == 0
    row = await _brief_row(session, brief_id)
    assert row.status == "planned" and row.final_report is None


async def test_finalize_provider_error(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    _mocked(monkeypatch, MockProvider(errors=[ProviderError("down")]))
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 500, res.text
    row = await _brief_row(session, brief_id)
    assert row.status == "planned" and row.final_report is None


async def test_finalize_ineligible_states(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)

    # Working task: refused before any LLM call.
    brief_id, _, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    provider = MockProvider(texts=[_final_text(tasks)])
    _mocked(monkeypatch, provider)
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "conflict"
    assert len(provider.calls) == 0

    # Pending approval: tasks settled, gate undecided.
    await _settle(session, tasks, [])
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks)]))
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 409, res.text

    # Not planned at all: fresh questions_ready brief.
    _mocked(monkeypatch, MockProvider(texts=["x"]))
    from tests.test_planning import _make_plannable as _ready

    fresh_id, _ = await _ready(api_client, headers, monkeypatch)
    res = await api_client.post(_final_url(fresh_id), headers=headers)
    assert res.status_code == 409, res.text


async def test_finalize_honest_failed_tasks(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    failed = {tasks[0]["id"]}
    await _settle(session, tasks, approvals, failed_ids=failed)
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks, failed_ids=failed)]))
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [t["id"] for t in body["report"]["failed_tasks"]] == [tasks[0]["id"]]
    assert body["report"]["failed_tasks"][0]["reason"] == "Ran out of attempts"
    assert {t["id"] for t in body["report"]["completed_tasks"]} == {tasks[1]["id"]}


async def test_finalize_repeat_no_extra_llm(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    provider = MockProvider(texts=[_final_text(tasks)])
    _mocked(monkeypatch, provider)

    first = await api_client.post(_final_url(brief_id), headers=headers)
    assert first.status_code == 200, first.text
    second = await api_client.post(_final_url(brief_id), headers=headers)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert len(provider.calls) == 1
    brains, events = await _counts(session, project_id)
    assert brains == 1 and events == 1


async def test_finalize_concurrent_one_batch(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks), _final_text(tasks)]))
    first, second = await asyncio.gather(
        api_client.post(_final_url(brief_id), headers=headers),
        api_client.post(_final_url(brief_id), headers=headers),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["report"] == second.json()["report"]
    brains, events = await _counts(session, project_id)
    assert brains == 1 and events == 1


async def test_finalize_rollback_on_brain_failure(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks)]))

    from app.persistence.repositories.concrete import BrainRepository

    def boom(self, session, business_id, data):  # type: ignore[no-untyped-def]
        raise RuntimeError("brain store down")

    monkeypatch.setattr(BrainRepository, "create", boom)
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 500, res.text
    brains, events = await _counts(session, project_id)
    assert brains == 0 and events == 0
    row = await _brief_row(session, brief_id)
    assert row.status == "planned" and row.final_report is None


async def test_finalize_rollback_on_event_failure(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks)]))

    from app.persistence.repositories.concrete import ActivityRepository as ActivityRepo

    def boom(self, session, business_id, data):  # type: ignore[no-untyped-def]
        raise RuntimeError("audit store down")

    monkeypatch.setattr(ActivityRepo, "append", boom)
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 500, res.text
    # Brain inserts rolled back with the batch: never partial learnings.
    brains, events = await _counts(session, project_id)
    assert brains == 0 and events == 0
    row = await _brief_row(session, brief_id)
    assert row.status == "planned" and row.final_report is None


async def test_finalize_revalidates_race(
    session: AsyncSession,
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    test_engine,
) -> None:
    """A task reopened during LLM generation must not finalize."""
    from app.persistence.engine import session_factory

    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)

    provider = MockProvider(texts=[_final_text(tasks)])
    real_complete = provider.complete

    async def reopening(request):  # type: ignore[no-untyped-def]
        factory = session_factory(test_engine)
        async with factory() as fresh:
            updated = await _tasks.update(fresh, BIZ, tasks[0]["id"], {"status": "working"})
            assert updated is not None
            await fresh.commit()
        return await real_complete(request)

    monkeypatch.setattr(provider, "complete", reopening)
    _mocked(monkeypatch, provider)
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 409, res.text
    brains, events = await _counts(session, project_id)
    assert brains == 0 and events == 0
    row = await _brief_row(session, brief_id)
    assert row.status == "planned" and row.final_report is None


async def test_finalize_auth_and_scope(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _, tasks, approvals = await _make_planned(api_client, headers, monkeypatch)
    await _settle(session, tasks, approvals)
    url = _final_url(brief_id)

    await make_business(session, biz_id=BIZ_OTHER)
    await session.commit()
    from tests.test_isolation import _login_as

    headers_b = await _login_as(session, api_client, "b@example.com", "pw-b-pw-b", BIZ_OTHER)
    foreign = await api_client.post(url, headers=headers_b)
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"

    headers = {"X-CSRF-Token": await login_as(api_client)}
    missing = await api_client.post(_final_url("nope"), headers=headers)
    assert missing.status_code == 404


async def test_finalize_anonymous_401(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    assert (await api_client.post(_final_url("whatever"))).status_code == 401


async def test_finalize_csrf_403(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session)
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    _mocked(monkeypatch, MockProvider(texts=[VALID_TEXT]))
    created = await api_client.post(
        _brief_url(), json={"goal": GOAL}, headers={"X-CSRF-Token": csrf}
    )
    url = _final_url(created.json()["id"])
    assert (await api_client.post(url)).status_code == 403
    bad = await api_client.post(url, headers={"X-CSRF-Token": "nope"})
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "forbidden"
