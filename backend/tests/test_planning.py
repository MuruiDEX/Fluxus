"""Work-plan materialization: brief_ready → project graph (Stage 8.3).

POST .../briefs/{id}/plan runs one bounded Team-Lead generation (at most two
LLM calls) OUTSIDE any database transaction, then creates the project, its
tasks, dependency edges, and gate approvals plus the brief_ready → planned
transition in ONE transaction. Any failure rolls everything back; concurrent
planners serialize on the brief row — exactly one graph wins. Created tasks
are never started here.
"""

import asyncio
import json as _json

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.mock import MockProvider
from app.llm.types import ProviderError
from app.persistence.models.task import TaskDependency
from app.persistence.repositories.concrete import (
    ApprovalRepository,
    ProjectRepository,
    TaskRepository,
)
from app.teamlead.planning import generate_plan, normalize_plan, validate_plan
from app.teamlead.prompts import build_plan_user_prompt
from app.teamlead.questions import BriefGenerationError
from tests.conftest import login_as
from tests.factories import BIZ, BIZ_OTHER, make_agent, make_business, make_world
from tests.test_briefs import ANSWERS, SUMMARY_TEXT, VALID_TEXT, _authed, _brief_url, _mocked

_projects = ProjectRepository()
_tasks = TaskRepository()
_approvals = ApprovalRepository()

PLAN_TEXT = _json.dumps(
    {
        "project": {
            "title": "Launch campaign",
            "goal": "Grow fast",
            "budget": 1000,
            "ai_budget": 100,
            "deadline": "30 May 2026",
        },
        "tasks": [
            {
                "key": "design",
                "title": "Design landing",
                "owner_agent_id": "ag_1",
                "summary": "Mockups",
                "eta_minutes": 60,
            },
            {
                "key": "build",
                "title": "Build landing",
                "owner_agent_id": "ag_1",
                "depends_on": ["design"],
                "gate": {
                    "title": "Approve copy",
                    "requested_by_agent_id": "ag_1",
                    "what": "Landing copy",
                },
            },
        ],
    }
)


def _plan() -> dict:
    return _json.loads(PLAN_TEXT)


def _plan_url(brief_id: str, biz: str = BIZ) -> str:
    return f"{_brief_url(biz)}/{brief_id}/plan"


async def _make_plannable(
    api_client: httpx.AsyncClient,
    headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    plan_texts: list[str] | None = None,
) -> tuple[str, MockProvider]:
    """Drive a brief to brief_ready through the real endpoints; arm plan mock."""
    _mocked(monkeypatch, MockProvider(texts=[VALID_TEXT]))
    created = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)
    assert created.status_code == 200, created.text
    brief_id = created.json()["id"]
    _mocked(monkeypatch, MockProvider(texts=[SUMMARY_TEXT]))
    answered = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "brief_ready"
    provider = MockProvider(texts=list(plan_texts if plan_texts is not None else [PLAN_TEXT]))
    _mocked(monkeypatch, provider)
    return brief_id, provider


async def _edge_count(session: AsyncSession) -> int:
    res = await session.execute(select(func.count()).select_from(TaskDependency))
    return int(res.scalar_one())


def test_plan_prompt_contains_roster_not_secrets() -> None:
    text = build_plan_user_prompt(
        "Grow fast",
        {"goal": "Grow fast"},
        [{"id": "ag_1", "name": "A", "role": "Tester"}],
    )
    assert "Grow fast" in text and "ag_1" in text and "Tester" in text


def test_validate_plan_accepts_valid() -> None:
    assert validate_plan(_plan()) == []


def test_validate_plan_rejects_shapes() -> None:
    assert validate_plan({}) != []
    assert validate_plan({"project": {"title": "x"}, "tasks": []}) != []
    many = _plan()
    many["tasks"] = [{"key": f"t{i}", "title": "x", "owner_agent_id": "ag_1"} for i in range(21)]
    assert validate_plan(many) != []
    dup = _plan()
    dup["tasks"][1]["key"] = "design"
    assert validate_plan(dup) != []
    unknown = _plan()
    unknown["tasks"][1]["depends_on"] = ["nope"]
    assert validate_plan(unknown) != []
    selfdep = _plan()
    selfdep["tasks"][0]["depends_on"] = ["design"]
    assert validate_plan(selfdep) != []
    loop = _plan()
    loop["tasks"][0]["depends_on"] = ["build"]
    assert validate_plan(loop) != []
    bad_gate = _plan()
    bad_gate["tasks"][0]["gate_at"] = 101
    assert validate_plan(bad_gate) != []
    neg_gate = _plan()
    neg_gate["tasks"][0]["gate_at"] = -1
    assert validate_plan(neg_gate) != []
    neg_money = _plan()
    neg_money["project"]["budget"] = -5
    assert validate_plan(neg_money) != []
    str_money = _plan()
    str_money["project"]["budget"] = "lots"
    assert validate_plan(str_money) != []


def test_validate_plan_rejects_protected_fields() -> None:
    smuggled = _plan()
    smuggled["tasks"][0]["status"] = "completed"
    assert validate_plan(smuggled) != []
    smuggled = _plan()
    smuggled["tasks"][0]["business_id"] = BIZ
    assert validate_plan(smuggled) != []
    smuggled = _plan()
    smuggled["project"]["team_ids"] = ["ag_1"]
    assert validate_plan(smuggled) != []
    smuggled = _plan()
    smuggled["tasks"][1]["gate"]["status"] = "approved"
    assert validate_plan(smuggled) != []
    smuggled = _plan()
    smuggled["tasks"][0]["id"] = "t_evil"
    assert validate_plan(smuggled) != []


def test_normalize_plan_allowlist_and_defaults() -> None:
    out = normalize_plan(_plan())
    assert out["tasks"][0]["gate_at"] == 100
    assert out["tasks"][1]["depends_on"] == ["design"]
    assert out["tasks"][1]["gate"]["title"] == "Approve copy"
    assert "status" not in out["tasks"][0] and "team_ids" not in out["project"]


async def test_generate_plan_success_single_call() -> None:
    provider = MockProvider(texts=[PLAN_TEXT])
    out = await generate_plan(provider, Settings(), "Grow fast", {"goal": "x"}, [])
    assert out["project"]["title"] == "Launch campaign"
    assert [t["key"] for t in out["tasks"]] == ["design", "build"]
    assert len(provider.calls) == 1


async def test_generate_plan_retries_then_succeeds() -> None:
    provider = MockProvider(texts=['{"bogus": 1}', PLAN_TEXT])
    out = await generate_plan(provider, Settings(), "Grow fast", {"goal": "x"}, [])
    assert out["project"]["title"] == "Launch campaign"
    assert len(provider.calls) == 2


async def test_generate_plan_exhaustion_raises() -> None:
    provider = MockProvider(texts=['{"bogus": 1}', '{"bogus": 2}'])
    with pytest.raises(BriefGenerationError):
        await generate_plan(provider, Settings(), "Grow fast", {"goal": "x"}, [])
    assert len(provider.calls) == 2


async def test_generate_plan_propagates_cancel() -> None:
    provider = MockProvider(texts=[PLAN_TEXT], latency_s=30.0)
    task = asyncio.ensure_future(
        generate_plan(provider, Settings(), "Grow fast", {"goal": "x"}, [])
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_create_plan_ok(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_plannable(api_client, headers, monkeypatch)

    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["briefId"] == brief_id
    project = body["project"]
    assert project["title"] == "Launch campaign"
    assert project["goal"] == "Grow fast"
    assert project["status"] == "planning"
    assert project["budget"] == "1000.00" and project["aiBudget"] == "100.00"
    assert project["aiSpent"] == "0" and project["progress"] == 0
    assert project["deadline"] == "30 May 2026"
    assert project["teamIds"] == ["ag_1"]
    assert len(provider.calls) == 1

    # Tasks are created not_started and never auto-started.
    assert len(body["tasks"]) == 2
    by_title = {t["title"]: t for t in body["tasks"]}
    assert set(t["status"] for t in body["tasks"]) == {"not_started"}
    assert all(t["attempts"] == 0 and t["costAccrued"] == "0" for t in body["tasks"])
    assert by_title["Build landing"]["ownerAgentId"] == "ag_1"
    assert by_title["Build landing"]["gateAt"] == 100

    # Gate approval links both directions.
    assert len(body["approvals"]) == 1
    approval = body["approvals"][0]
    assert approval["status"] == "pending"
    assert approval["title"] == "Approve copy"
    assert approval["projectId"] == project["id"]
    assert approval["gatesTaskId"] == by_title["Build landing"]["id"]
    assert by_title["Build landing"]["approvalGateId"] == approval["id"]
    assert by_title["Design landing"]["approvalGateId"] is None

    # Dependency edge persisted.
    assert await _edge_count(session) == 1

    # Brief transitioned with the project link.
    brief = await api_client.get(f"{_brief_url()}/{brief_id}", headers=headers)
    assert brief.json()["status"] == "planned"
    assert brief.json()["projectId"] == project["id"]


async def test_create_plan_too_many_tasks(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    big = _plan()
    big["tasks"] = [{"key": f"t{i}", "title": f"T{i}", "owner_agent_id": "ag_1"} for i in range(21)]
    brief_id, provider = await _make_plannable(
        api_client, headers, monkeypatch, plan_texts=[_json.dumps(big)]
    )
    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"
    assert await _projects.count(session, BIZ) == 1  # only proj_1 from make_world
    assert len(provider.calls) <= 2  # bounded generation budget, then stop
    row = await _brief_status(session, brief_id)
    assert row == ("brief_ready", None)


async def _brief_status(session: AsyncSession, brief_id: str) -> tuple[str, str | None]:
    from app.persistence.repositories.concrete import BriefRepository

    row = await BriefRepository().get(session, BIZ, brief_id)
    assert row is not None
    return row.status, row.project_id


async def test_create_plan_unknown_and_foreign_agent(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    await make_business(session, biz_id=BIZ_OTHER)
    await make_agent(session, "ag_x", BIZ_OTHER)
    await session.commit()

    bad = _plan()
    bad["tasks"][0]["owner_agent_id"] = "ag_ghost"
    brief_id, _ = await _make_plannable(
        api_client, headers, monkeypatch, plan_texts=[_json.dumps(bad)]
    )
    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "dangling_reference"

    foreign = _plan()
    foreign["tasks"][0]["owner_agent_id"] = "ag_x"
    brief_id, _ = await _make_plannable(
        api_client, headers, monkeypatch, plan_texts=[_json.dumps(foreign)]
    )
    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "dangling_reference"

    assert await _projects.count(session, BIZ) == 1
    assert await _tasks.count(session, BIZ) == 0
    assert await _approvals.count(session, BIZ) == 0


async def test_create_plan_bad_graph_and_gates(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    cases = []
    unknown = _plan()
    unknown["tasks"][1]["depends_on"] = ["nope"]
    cases.append(unknown)
    selfdep = _plan()
    selfdep["tasks"][0]["depends_on"] = ["design"]
    cases.append(selfdep)
    loop = _plan()
    loop["tasks"][0]["depends_on"] = ["build"]
    cases.append(loop)
    bad_gate = _plan()
    bad_gate["tasks"][0]["gate_at"] = 101
    cases.append(bad_gate)
    neg_money = _plan()
    neg_money["project"]["budget"] = -5
    cases.append(neg_money)
    for bad in cases:
        brief_id, _ = await _make_plannable(
            api_client, headers, monkeypatch, plan_texts=[_json.dumps(bad)]
        )
        res = await api_client.post(_plan_url(brief_id), headers=headers)
        assert res.status_code == 422, (bad, res.text)
        assert res.json()["error"]["code"] == "validation_error"
    assert await _projects.count(session, BIZ) == 1
    assert await _brief_status(session, brief_id) == ("brief_ready", None)


async def test_create_plan_invalid_json_then_exhaustion(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)

    # First generation malformed, second valid: retry succeeds.
    brief_id, provider = await _make_plannable(
        api_client, headers, monkeypatch, plan_texts=["not json at all", PLAN_TEXT]
    )
    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 200, res.text
    assert len(provider.calls) == 2

    # Both generations unusable: 422, budget spent, nothing stored.
    brief_id, provider = await _make_plannable(
        api_client, headers, monkeypatch, plan_texts=["not json", '{"bogus": 1}']
    )
    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 422, res.text
    assert len(provider.calls) == 2
    assert await _projects.count(session, BIZ) == 2  # proj_1 + the retried plan
    assert await _brief_status(session, brief_id) == ("brief_ready", None)


async def test_create_plan_provider_error(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_plannable(api_client, headers, monkeypatch, plan_texts=[PLAN_TEXT])
    _mocked(monkeypatch, MockProvider(errors=[ProviderError("down")]))
    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 500, res.text
    assert await _brief_status(session, brief_id) == ("brief_ready", None)


async def test_create_plan_rollback_on_approval_failure(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-transaction failure rolls back project + tasks + brief transition."""
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_plannable(api_client, headers, monkeypatch)

    def boom(self, session, business_id, data):  # type: ignore[no-untyped-def]
        raise RuntimeError("approval store down")

    monkeypatch.setattr(ApprovalRepository, "create", boom)
    res = await api_client.post(_plan_url(brief_id), headers=headers)
    assert res.status_code == 500, res.text
    assert await _projects.count(session, BIZ) == 1
    assert await _tasks.count(session, BIZ) == 0
    assert await _approvals.count(session, BIZ) == 0
    assert await _edge_count(session) == 0
    assert await _brief_status(session, brief_id) == ("brief_ready", None)


async def test_create_plan_concurrent_one_graph(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_plannable(
        api_client, headers, monkeypatch, plan_texts=[PLAN_TEXT, PLAN_TEXT]
    )
    first, second = await asyncio.gather(
        api_client.post(_plan_url(brief_id), headers=headers),
        api_client.post(_plan_url(brief_id), headers=headers),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["project"]["id"] == second.json()["project"]["id"]
    assert await _projects.count(session, BIZ) == 2  # proj_1 + exactly one plan
    assert await _tasks.count(session, BIZ) == 2
    assert await _approvals.count(session, BIZ) == 1
    assert await _edge_count(session) == 1


async def test_create_plan_repeat_no_extra_llm(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_plannable(api_client, headers, monkeypatch)

    first = await api_client.post(_plan_url(brief_id), headers=headers)
    assert first.status_code == 200, first.text
    assert len(provider.calls) == 1

    second = await api_client.post(_plan_url(brief_id), headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["project"]["id"] == first.json()["project"]["id"]
    assert {t["id"] for t in second.json()["tasks"]} == {t["id"] for t in first.json()["tasks"]}
    assert len(provider.calls) == 1
    assert await _projects.count(session, BIZ) == 2


async def test_create_plan_anonymous_401(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await session.commit()
    res = await api_client.post(_plan_url("whatever"))
    assert res.status_code == 401


async def test_create_plan_csrf_403(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_plannable(api_client, headers, monkeypatch)
    assert (await api_client.post(_plan_url(brief_id))).status_code == 403
    bad = await api_client.post(_plan_url(brief_id), headers={"X-CSRF-Token": "nope"})
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "forbidden"


async def test_create_plan_scope_404_and_wrong_status(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_plannable(api_client, headers, monkeypatch)

    await make_business(session, biz_id=BIZ_OTHER)
    await session.commit()
    from tests.test_isolation import _login_as

    headers_b = await _login_as(session, api_client, "b@example.com", "pw-b-pw-b", BIZ_OTHER)
    foreign = await api_client.post(_plan_url(brief_id), headers=headers_b)
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"

    # _login_as replaced the jar cookie with B's session — log back in as A.
    headers = {"X-CSRF-Token": await login_as(api_client)}
    missing = await api_client.post(_plan_url("nope"), headers=headers)
    assert missing.status_code == 404

    # A brief that never left questions_ready is not plannable.
    _mocked(monkeypatch, MockProvider(texts=[VALID_TEXT]))
    created = await api_client.post(_brief_url(), json={"goal": "Grow"}, headers=headers)
    early = await api_client.post(_plan_url(created.json()["id"]), headers=headers)
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "conflict"
