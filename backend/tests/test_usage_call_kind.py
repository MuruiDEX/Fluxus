"""Stage 16.1 slice 1: validated `call_kind` on the usage ledger + summary."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.enums import UsageCallKind
from app.persistence.repositories.usage import UsageRepository
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, make_task, make_world

_usage = UsageRepository()


def _url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/usage"


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> str:
    await seed_operator(session)
    return await login_as(api_client)


def _row(**over) -> dict:
    data = {
        "idempotency_key": "usage:k1",
        "attempt_key": f"{BIZ}:t_1:a0",
        "attempt": 0,
        "provider": "mock",
        "model": "mock",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost_amount": Decimal("0.10"),
        "cost_source": "tariff",
        "usage_status": "confirmed",
        "occurred_at": datetime.now(UTC),
    }
    data.update(over)
    return data


def test_call_kind_literals_are_exactly_the_billable_paths() -> None:
    assert {kind.value for kind in UsageCallKind} == {
        "task_execution",
        "brief_questions",
        "brief_summary",
        "plan_generation",
        "brief_finalize",
    }


async def test_append_persists_and_api_exposes_call_kind(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="completed")
    await _usage.append(
        session,
        BIZ,
        _row(
            id="u-task",
            idempotency_key="u-task",
            project_id="proj_1",
            task_id="t_1",
            call_kind="task_execution",
        ),
    )
    await _usage.append(
        session,
        BIZ,
        _row(
            id="u-final",
            idempotency_key="u-final",
            project_id="proj_1",
            task_id=None,
            call_kind="brief_finalize",
            cost_amount=Decimal("0.07"),
        ),
    )
    await _usage.append(
        session,
        BIZ,
        _row(
            id="u-plan",
            idempotency_key="u-plan",
            attempt_key="tl:b1:plan_generation:g0",
            project_id=None,
            task_id=None,
            call_kind="plan_generation",
            cost_amount=Decimal("0.03"),
            usage_status="estimated",
        ),
    )
    await _usage.append(
        session,
        BIZ,
        _row(
            id="u-legacy",
            idempotency_key="u-legacy",
            attempt_key="tl:b1:brief_summary:g0",
            project_id=None,
            task_id=None,
            call_kind=None,
            cost_source="unknown",
            usage_status="unknown",
            cost_amount=None,
        ),
    )
    await session.commit()
    await _authed(session, api_client)

    res = await api_client.get(_url())

    assert res.status_code == 200, res.text
    body = res.json()
    kinds = {item["id"]: item["callKind"] for item in body["items"]}
    assert kinds["u-task"] == "task_execution"
    assert kinds["u-final"] == "brief_finalize"
    assert kinds["u-plan"] == "plan_generation"
    assert kinds["u-legacy"] is None

    by_kind = body["summary"]["byCallKind"]
    assert by_kind["task_execution"]["confirmedCost"] == "0.10"
    assert by_kind["brief_finalize"]["confirmedCost"] == "0.07"
    assert by_kind["plan_generation"]["estimatedCost"] == "0.03"
    # Legacy NULL kinds are grouped under the empty key, never dropped.
    assert by_kind[""]["unknownCount"] == 1


async def test_totals_by_call_kind_respects_scope(session: AsyncSession) -> None:
    await make_world(session)
    await make_task(session, status="completed")
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-scoped",
            project_id="proj_1",
            task_id="t_1",
            call_kind="task_execution",
        ),
    )
    await _usage.append(
        session,
        BIZ,
        _row(
            idempotency_key="u-business",
            attempt_key="tl:b1:brief_questions:g0",
            project_id=None,
            task_id=None,
            call_kind="brief_questions",
        ),
    )

    scoped = await _usage.totals_by_call_kind(session, BIZ, project_id="proj_1")
    assert set(scoped) == {"task_execution"}
    assert scoped["task_execution"]["confirmed"] == (Decimal("0.10"), 1)

    everything = await _usage.totals_by_call_kind(session, BIZ)
    assert set(everything) == {"task_execution", "brief_questions"}
