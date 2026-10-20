"""Stage 16 slice 6: usage ledger read API + allowUnpricedAi patch."""

from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_task, make_world
from tests.factories import projects as projects_repo

_usage = UsageRepository()
_reservations = BudgetReservationRepository()


def _url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/usage"


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> str:
    await seed_operator(session)
    return await login_as(api_client)


async def _priced(session: AsyncSession, key: str, cost: str, status: str = "confirmed") -> None:
    await _usage.append(
        session,
        BIZ,
        {
            "idempotency_key": key,
            "attempt_key": f"{BIZ}:t_1:a0",
            "attempt": 0,
            "project_id": "proj_1",
            "task_id": "t_1",
            "provider": "mock",
            "model": "mock",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost_amount": Decimal(cost) if cost is not None else None,
            "cost_source": "tariff" if cost is not None else "unknown",
            "usage_status": status,
        },
    )


async def test_usage_empty(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    await _authed(session, api_client)

    res = await api_client.get(_url())

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["items"] == []
    assert body["summary"]["confirmedCost"] == "0"
    assert body["summary"]["unknownCount"] == 0


async def test_usage_lists_priced_and_unknown(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="completed")
    await _priced(session, "u1", "0.10")
    await _usage.append(
        session,
        BIZ,
        {
            "idempotency_key": "u2",
            "attempt_key": f"{BIZ}:t_1:a1",
            "attempt": 1,
            "project_id": "proj_1",
            "task_id": "t_1",
            "provider": "mock",
            "model": "mock",
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
            "cost_amount": None,
            "cost_source": "unknown",
            "usage_status": "unknown",
        },
    )
    await session.commit()
    await _authed(session, api_client)

    res = await api_client.get(_url())

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    by_source = {i["costSource"]: i for i in body["items"]}
    assert by_source["tariff"]["costAmount"] == "0.10"
    assert by_source["tariff"]["occurredAt"]
    assert by_source["unknown"]["costAmount"] is None
    assert body["summary"]["confirmedCost"] == "0.10"
    assert body["summary"]["unknownCount"] == 1


async def test_usage_reservation_summary(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="working")
    repo = _reservations
    active = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=0,
        attempt_key=f"{BIZ}:t_1:a0",
        amount=Decimal("2.00"),
    )
    ambiguous = await repo.reserve(
        session,
        BIZ,
        project_id="proj_1",
        task_id="t_1",
        attempt=1,
        attempt_key=f"{BIZ}:t_1:a1",
        amount=Decimal("0.50"),
    )
    assert active is not None and ambiguous is not None
    await repo.set_status(
        session,
        BIZ,
        ambiguous.id,
        from_statuses=("active",),
        to_status="ambiguous",
        reason="timeout",
    )
    await session.commit()
    await _authed(session, api_client)

    res = await api_client.get(_url())

    assert res.status_code == 200, res.text
    reservations = res.json()["summary"]["reservations"]
    assert reservations["active"] == "2.00"
    assert reservations["ambiguous"] == "0.50"


async def test_usage_filters_and_foreign_scope(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await make_task(session, status="completed")
    await _priced(session, "u1", "0.10")
    await session.commit()
    await _authed(session, api_client)

    scoped = await api_client.get(f"{_url()}?project_id=proj_1&task_id=t_1")
    assert scoped.status_code == 200
    assert scoped.json()["total"] == 1

    missing = await api_client.get(f"{_url()}?project_id=ghost")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    foreign = await api_client.get(_url(BIZ_OTHER))
    assert foreign.status_code == 404


async def test_usage_requires_auth(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    res = await api_client.get(_url())
    assert res.status_code == 401


async def test_project_patch_allow_unpriced_ai(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await session.commit()
    csrf = await _authed(session, api_client)

    res = await api_client.patch(
        f"/api/v1/businesses/{BIZ}/projects/proj_1",
        json={"allowUnpricedAi": True},
        headers={"X-CSRF-Token": csrf},
    )

    assert res.status_code == 200, res.text
    assert res.json()["allowUnpricedAi"] is True
    project = await projects_repo.get(session, BIZ, "proj_1")
    assert project is not None and project.allow_unpriced_ai is True
