"""Stage 16.1 slice 5: executor post-send malformed-response policy.

A provider that answers with an unusable body may already have billed the
request. The attempt is therefore priced `estimated` from the tariff bound when
possible, or its hold is kept `ambiguous`; it is never released as free and
never recorded as a zero-cost success.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.mock import MockProvider
from app.llm.types import BadResponseError
from app.persistence.repositories.concrete import ProjectRepository, TaskRepository
from app.usage import pricing
from tests.factories import BIZ, make_task, make_world
from tests.test_budget_enforcement import (
    TARIFF,
    _request,
    _reservations,
    _run,
    _usages,
)


async def test_bad_response_with_tariff_records_estimated(
    session: AsyncSession,
) -> None:
    pricing.register_tariff(TARIFF)
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(session, BIZ, "proj_1", {"ai_budget": Decimal("100.00")})
    provider = MockProvider(errors=[BadResponseError("malformed body")])

    result = await _run(session, provider, _request())

    assert result.outcome == "failed"
    assert result.error_code == "bad_response"
    usages = await _usages(session)
    assert len(usages) == 1
    row = usages[0]
    assert row.usage_status == "estimated"
    assert row.cost_source == "tariff"
    assert row.cost_amount is not None and row.cost_amount > 0
    assert row.call_kind == "task_execution"
    assert row.project_id == "proj_1" and row.task_id == "t_1"
    holds = await _reservations(session)
    assert len(holds) == 1 and holds[0].status == "consumed"

    # Estimated cost is tracked separately: never confirmed spend.
    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")
    task = await TaskRepository().get(session, BIZ, "t_1")
    assert task is not None and task.cost_accrued == Decimal("0")


async def test_bad_response_without_tariff_keeps_hold_ambiguous(
    session: AsyncSession,
) -> None:
    await make_world(session)
    await make_task(session, status="not_started")
    await ProjectRepository().update(
        session,
        BIZ,
        "proj_1",
        {"ai_budget": Decimal("5.00"), "allow_unpriced_ai": True},
    )
    provider = MockProvider(errors=[BadResponseError("malformed body")])

    result = await _run(session, provider, _request())

    assert result.outcome == "failed"
    assert result.error_code == "bad_response"
    assert await _usages(session) == []
    holds = await _reservations(session)
    assert len(holds) == 1
    assert holds[0].status == "ambiguous"
    assert holds[0].reason == "post_send_unpriced"

    project = await ProjectRepository().get(session, BIZ, "proj_1")
    assert project is not None and project.ai_spent == Decimal("0")
