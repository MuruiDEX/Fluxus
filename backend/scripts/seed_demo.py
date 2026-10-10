"""Demo seed for local development — Northstar Fitness subset (dev/test only).

Idempotent: skips when the demo business already exists. Mirrors the ids in
`frontend/lib/data/seed-northstar.ts` so API trials line up with the UI demo.
Never run against production data.
"""

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.persistence.engine import create_engine_from_url, session_factory  # noqa: E402
from app.persistence.models.agent import Agent  # noqa: E402
from app.persistence.models.approval import Approval  # noqa: E402
from app.persistence.models.business import Business  # noqa: E402
from app.persistence.models.project import Project  # noqa: E402
from app.persistence.models.task import Task, TaskDependency  # noqa: E402
from app.persistence.repositories.spend import recalculate_project_spend  # noqa: E402

DEV_URL = os.environ.get("APP_DATABASE_URL", "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse")


async def main() -> None:
    engine = create_engine_from_url(DEV_URL)
    factory = session_factory(engine)
    async with factory() as session:
        async with session.begin():
            exists = await session.get(Business, "biz_northstar")
            if exists is not None:
                print("demo seed already present — skipping")
                return
            # NOTE: flush incrementally, one aggregate at a time. A single
            # flush of FK-related rows across tables is not used anywhere:
            # repositories sequence their writes the same way.
            session.add(
                Business(
                    id="biz_northstar",
                    name="Northstar Fitness",
                    tagline="Modern fitness studio · Kraków",
                    industry="Fitness & Wellness",
                    location="Kraków, Poland",
                    owner_name="Alex",
                    health_score=87,
                )
            )
            await session.flush()
            session.add(
                Agent(
                    id="ag_lead",
                    business_id="biz_northstar",
                    name="Atlas",
                    role="Team Lead",
                    status="working",
                    hired=True,
                )
            )
            session.add(
                Agent(
                    id="ag_market",
                    business_id="biz_northstar",
                    name="Mira",
                    role="Market Analyst",
                    status="working",
                    hired=True,
                )
            )
            await session.flush()
            session.add(
                Project(
                    id="proj_krakow_launch",
                    business_id="biz_northstar",
                    title="Launch Kraków Fitness Studio",
                    goal="Open a modern fitness studio in Kraków.",
                    status="in_progress",
                    budget=Decimal("20000"),
                    ai_budget=Decimal("20"),
                    team_ids=["ag_lead", "ag_market"],
                )
            )
            await session.flush()
            session.add(
                Task(
                    id="t_market",
                    business_id="biz_northstar",
                    project_id="proj_krakow_launch",
                    title="Market Research",
                    owner_agent_id="ag_market",
                    status="completed",
                    progress=100,
                    cost_accrued=Decimal("1.80"),
                )
            )
            await session.flush()
            session.add(
                Task(
                    id="t_plan",
                    business_id="biz_northstar",
                    project_id="proj_krakow_launch",
                    title="Launch Preparation",
                    owner_agent_id="ag_lead",
                    status="working",
                    progress=10,
                    cost_accrued=Decimal("0.40"),
                )
            )
            await session.flush()
            session.add(TaskDependency(task_id="t_plan", depends_on_id="t_market"))
            await session.flush()
            session.add(
                Approval(
                    id="ap_ads",
                    business_id="biz_northstar",
                    project_id="proj_krakow_launch",
                    title="Publish Google Ads campaign",
                    requested_by="Marketing Manager",
                    requested_by_agent_id="ag_lead",
                    status="pending",
                    cost="$1,200 (30 days)",
                )
            )
            await session.flush()
            total = await recalculate_project_spend(session, "biz_northstar", "proj_krakow_launch")
            check = await session.execute(
                select(Task.id).where(Task.id == "t_market", Task.business_id == "biz_northstar")
            )
            assert check.scalar_one() == "t_market"
            print(f"demo seed inserted — project ai_spent={total}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
