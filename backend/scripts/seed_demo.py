"""Demo seed for local development — Northstar Fitness subset (dev/test only).

Idempotent: skips when the demo business already exists. Mirrors the ids in
`frontend/lib/data/seed-northstar.ts` so API trials line up with the UI demo.
Never run against production data.

Requires APP_ALLOW_DEMO_SEED=1. A non-loopback database also requires
APP_ALLOW_REMOTE_DEMO_SEED=1 and is intended only for an isolated dev/test DB.
"""

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.core.script_safety import UnsafeScriptError, guard_development_script  # noqa: E402
from app.persistence.engine import create_engine_from_url, session_factory  # noqa: E402
from app.persistence.models.agent import Agent  # noqa: E402
from app.persistence.models.approval import Approval  # noqa: E402
from app.persistence.models.business import Business  # noqa: E402
from app.persistence.models.marketplace_listing import MarketplaceListing  # noqa: E402
from app.persistence.models.project import Project  # noqa: E402
from app.persistence.models.task import Task, TaskDependency  # noqa: E402
from app.persistence.repositories.spend import recalculate_project_spend  # noqa: E402

DEV_URL = os.environ.get("APP_DATABASE_URL", "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse")

# Global catalog mirror of frontend/lib/data/seed-northstar.ts `marketplace`
# (slugs, categories, copy, capabilities, permissions, prices). Idempotent:
# existing slugs are left untouched so re-runs never duplicate or reset.
CATALOG: list[dict] = [
    {
        "id": "fitness-pack",
        "name": "Fitness Business Pack",
        "role": "Business pack",
        "blurb": "Complete AI team and tools to run a fitness business.",
        "price": "$99/month",
        "price_note": "one-click install",
        "rating": 4.9,
        "installs": "2.1k",
        "badge": "Popular",
        "capabilities": ["Sales AI", "Support AI", "Booking flow", "CRM pipeline"],
        "permissions": {
            "read": ["CRM"],
            "write": ["CRM"],
            "actions": ["Send email"],
            "denied": ["Spend over budget"],
        },
        "category": "pack",
    },
    {
        "id": "sales-manager",
        "name": "AI Sales Manager",
        "role": "Sales",
        "blurb": "Qualify leads, follow up, update CRM.",
        "price": "$29/month",
        "price_note": "per month",
        "rating": 4.9,
        "installs": "5.1k",
        "badge": None,
        "capabilities": ["Lead scoring", "Follow-up", "Reports"],
        "permissions": {
            "read": ["CRM", "Customer data"],
            "write": ["CRM"],
            "actions": ["Send email"],
            "denied": ["Change pricing"],
        },
        "category": "employee",
    },
    {
        "id": "marketing-manager",
        "name": "Marketing Manager",
        "role": "Marketing",
        "blurb": "Campaigns, content, ads.",
        "price": "$29/month",
        "price_note": "per month",
        "rating": 4.8,
        "installs": "3.2k",
        "badge": None,
        "capabilities": ["Campaigns", "Content", "Ads"],
        "permissions": {
            "read": ["CRM", "Analytics"],
            "write": ["Draft campaigns"],
            "actions": ["Prepare ads"],
            "denied": ["Publish without approval"],
        },
        "category": "employee",
    },
    {
        "id": "seo-specialist",
        "name": "SEO Specialist AI",
        "role": "SEO",
        "blurb": "Keywords, briefs, audits for local search.",
        "price": "$19/month",
        "price_note": "per month",
        "rating": 4.8,
        "installs": "1.8k",
        "badge": "New",
        "capabilities": ["Keywords", "Briefs", "Audits"],
        "permissions": {
            "read": ["Web", "Analytics"],
            "write": ["SEO briefs"],
            "actions": [],
            "denied": ["Edit production site"],
        },
        "category": "employee",
    },
    {
        "id": "support-ai",
        "name": "Customer Support AI",
        "role": "Support",
        "blurb": "Answer, classify, book, escalate.",
        "price": "$19/month",
        "price_note": "per month",
        "rating": 4.7,
        "installs": "4.4k",
        "badge": None,
        "capabilities": ["Chat", "Booking", "Escalation"],
        "permissions": {
            "read": ["Inbox", "Business Brain"],
            "write": ["Replies"],
            "actions": ["Reply"],
            "denied": ["Refund"],
        },
        "category": "employee",
    },
    {
        "id": "cfo-ai",
        "name": "CFO AI",
        "role": "Finance",
        "blurb": "Forecasts, budgets, pricing analysis.",
        "price": "$29/month",
        "price_note": "per month",
        "rating": 5.0,
        "installs": "2.7k",
        "badge": "Trending",
        "capabilities": ["Forecasts", "Break-even", "Pricing"],
        "permissions": {
            "read": ["Finance"],
            "write": ["Financial reports"],
            "actions": [],
            "denied": ["Move money"],
        },
        "category": "employee",
    },
]


async def main() -> None:
    try:
        guard_development_script(
            "seed_demo",
            DEV_URL,
            opt_in_env="APP_ALLOW_DEMO_SEED",
            allow_remote_env="APP_ALLOW_REMOTE_DEMO_SEED",
        )
    except UnsafeScriptError as exc:
        raise SystemExit(f"error: {exc}") from exc
    engine = create_engine_from_url(DEV_URL)
    factory = session_factory(engine)
    async with factory() as session:
        async with session.begin():
            # Global catalog first: independent of the demo business, so a
            # re-run backfills listings even when the business already exists.
            for row in CATALOG:
                if await session.get(MarketplaceListing, row["id"]) is None:
                    session.add(MarketplaceListing(**row))
            await session.flush()
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
