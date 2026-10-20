"""Stage 16.1 slice 4: every Team Lead provider path is metered.

End-to-end through the real brief endpoints: questions, summary, plan and
finalize each append exactly one ledger row with their own `call_kind`. Calls
that run before a project exists are business-scoped (project NULL) and never
enter project spend; finalization reserves against the project and is
budget-enforced.
"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.mock import MockProvider
from app.persistence.repositories.concrete import ProjectRepository
from app.persistence.repositories.usage import (
    BudgetReservationRepository,
    UsageRepository,
)
from app.usage import pricing
from app.usage.tariffs import Tariff
from tests.factories import BIZ
from tests.test_briefs import _authed, _mocked
from tests.test_finalize import _final_text, _final_url, _settle
from tests.test_planning import _make_plannable, _plan_url

_usage = UsageRepository()
_reservations = BudgetReservationRepository()

TARIFF = Tariff(
    provider="mock",
    model="mock",
    input_per_1k=Decimal("5.00"),
    output_per_1k=Decimal("10.00"),
)


async def _planned(
    api_client: httpx.AsyncClient,
    session: AsyncSession,
    headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str, list[dict], list[dict]]:
    brief_id, _ = await _make_plannable(api_client, headers, monkeypatch)
    planned = await api_client.post(_plan_url(brief_id), headers=headers)
    assert planned.status_code == 200, planned.text
    body = planned.json()
    project_id = body["project"]["id"]
    await _settle(session, body["tasks"], body["approvals"])
    return brief_id, project_id, body["tasks"], body["approvals"]


async def test_all_teamlead_paths_are_metered_with_call_kind(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pricing.register_tariff(TARIFF)
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, _ = await _planned(api_client, session, headers, monkeypatch)

    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks)]))
    res = await api_client.post(_final_url(brief_id), headers=headers)
    assert res.status_code == 200, res.text

    rows = await _usage.list_for_business(session, BIZ, limit=200)
    by_kind = {row.call_kind: row for row in rows}
    assert set(by_kind) == {
        "brief_questions",
        "brief_summary",
        "plan_generation",
        "brief_finalize",
    }
    for kind in ("brief_questions", "brief_summary", "plan_generation"):
        assert by_kind[kind].project_id is None
        assert by_kind[kind].task_id is None
        assert by_kind[kind].usage_status == "confirmed"
        assert by_kind[kind].cost_amount is not None and by_kind[kind].cost_amount > 0
    final = by_kind["brief_finalize"]
    assert final.project_id == project_id and final.task_id is None
    assert final.usage_status == "confirmed"

    project = await ProjectRepository().get(session, BIZ, project_id)
    assert project is not None
    # Only the taskless, project-scoped finalization counts as project spend.
    assert project.ai_spent == final.cost_amount
    holds = await _reservations.list_pending(session, BIZ, project_id=project_id)
    assert holds == []
    all_holds = await _reservations.status_totals(session, BIZ, project_id=project_id)
    # The hold is the pre-call upper bound; it settles to consumed.
    assert all_holds.get("consumed") is not None and all_holds["consumed"] > 0
    assert "released" not in all_holds

    project_usage = await api_client.get(
        f"/api/v1/businesses/{BIZ}/usage?project_id={project_id}", headers=headers
    )
    assert project_usage.status_code == 200, project_usage.text
    psummary = project_usage.json()["summary"]
    assert set(psummary["byCallKind"]) == {"brief_finalize"}

    business_usage = await api_client.get(f"/api/v1/businesses/{BIZ}/usage", headers=headers)
    bsummary = business_usage.json()["summary"]
    assert set(bsummary["byCallKind"]) == set(by_kind)
    for kind, item in bsummary["byCallKind"].items():
        assert item["confirmedCost"] == str(by_kind[kind].cost_amount)
    assert bsummary["confirmedCost"] == str(
        sum((row.cost_amount or Decimal("0")) for row in rows)
    )


async def test_finalize_is_budget_enforced_and_refused_before_send(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, _ = await _planned(api_client, session, headers, monkeypatch)
    provider = MockProvider(texts=[_final_text(tasks)])
    _mocked(monkeypatch, provider)

    res = await api_client.post(_final_url(brief_id), headers=headers)

    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "budget_unpriced"
    assert provider.calls == []
    rows = await _usage.list_for_business(session, BIZ, limit=200)
    assert all(row.call_kind != "brief_finalize" for row in rows)
    project = await ProjectRepository().get(session, BIZ, project_id)
    assert project is not None and project.ai_spent == Decimal("0")


async def test_finalize_opt_in_records_unknown_but_still_runs(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, project_id, tasks, _ = await _planned(api_client, session, headers, monkeypatch)
    await ProjectRepository().update(
        session, BIZ, project_id, {"allow_unpriced_ai": True}
    )
    await session.commit()
    _mocked(monkeypatch, MockProvider(texts=[_final_text(tasks)]))

    res = await api_client.post(_final_url(brief_id), headers=headers)

    assert res.status_code == 200, res.text
    rows = await _usage.list_for_business(session, BIZ, limit=200)
    final = [row for row in rows if row.call_kind == "brief_finalize"]
    assert len(final) == 1
    assert final[0].usage_status == "unknown"
    assert final[0].cost_amount is None
    project = await ProjectRepository().get(session, BIZ, project_id)
    assert project is not None and project.ai_spent == Decimal("0")
    holds = await _reservations.status_totals(session, BIZ, project_id=project_id)
    assert holds.get("consumed") == Decimal("1.00")
