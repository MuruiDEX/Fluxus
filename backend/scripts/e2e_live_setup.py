"""LIVE E2E environment setup (dev database only, never production).

Creates/resets isolated contexts for the Playwright LIVE journeys:
- business `biz_e2e` (+ membership for the e2e operator, nothing else);
- user `e2e@example.com` (password from E2E_PASSWORD, default `e2e-pass-1`);
- global catalog backfilled from seed_demo.CATALOG (no duplicates);
- any previous e2e hires (agents with source_slug in biz_e2e) removed so
  every run starts with a hireable catalog;
- deterministic core-workflow graph (Stage 10) built through the PRODUCTION
  repositories — the same rows POST /plan would persist, minus LLM calls:
  planned brief br_e2e + project proj_e2e (2 completed tasks, 1 pending
  gate approval), runnable project proj_e2e_run, and recovery project
  proj_e2e_rec with one provably-stale working task.

Usage (from backend/, dev postgres on :5432):
    $env:APP_DATABASE_URL="postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse"
    python scripts/e2e_live_setup.py

Idempotent: safe to run before every LIVE E2E pass.
"""

import asyncio
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select  # noqa: E402

from app.core.passwords import hash_password  # noqa: E402
from app.persistence.engine import create_engine_from_url, session_factory  # noqa: E402
from app.persistence.models.agent import Agent  # noqa: E402
from app.persistence.models.approval import Approval  # noqa: E402
from app.persistence.models.brain_entry import BrainEntry  # noqa: E402
from app.persistence.models.brief import Brief  # noqa: E402
from app.persistence.models.business import Business  # noqa: E402
from app.persistence.models.marketplace_listing import MarketplaceListing  # noqa: E402
from app.persistence.models.membership import Membership  # noqa: E402
from app.persistence.models.pack import PackInstallation, PackInstallationItem  # noqa: E402
from app.persistence.models.project import Project  # noqa: E402
from app.persistence.models.task import Task  # noqa: E402
from app.persistence.models.user import User  # noqa: E402
from app.persistence.repositories.concrete import (  # noqa: E402
    AgentRepository,
    BriefRepository,
    PlanningRepository,
    ProjectRepository,
    TaskRepository,
)
from scripts.seed_demo import CATALOG  # noqa: E402

DB_URL = os.environ.get("APP_DATABASE_URL", "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse")
BIZ = "biz_e2e"
EMAIL = os.environ.get("E2E_EMAIL", "e2e@example.com")
PASSWORD = os.environ.get("E2E_PASSWORD", "e2e-pass-1")

BRIEF_ID = "br_e2e"
AGENT_ID = "ag_e2e"

# Hand-written but validator-shaped: PlanningRepository.create_plan_graph
# consumes exactly these keys (same as the POST /plan path persists).
PLAN = {
    "project": {
        "title": "E2E Workflow Project",
        "goal": "Verify the LIVE core workflow end to end.",
        "budget": "1000",
        "ai_budget": "10",
    },
    "tasks": [
        {
            "key": "docs",
            "title": "Write workflow docs",
            "summary": "Document the verified LIVE path.",
            "owner_agent_id": AGENT_ID,
        },
        {
            "key": "deploy",
            "title": "Deploy workflow service",
            "summary": "Ship behind the approval gate.",
            "owner_agent_id": AGENT_ID,
            "gate": {
                "title": "Approve workflow deploy",
                "requested_by_agent_id": AGENT_ID,
                "requested_by": "E2E Engineer",
                "what": "Deploy the workflow service to staging.",
                "expected_result": "Staging deploy succeeds.",
                "rationale": "E2E gate coverage.",
            },
        },
    ],
}


async def main() -> None:
    engine = create_engine_from_url(DB_URL)
    factory = session_factory(engine)
    async with factory() as session:
        async with session.begin():
            for row in CATALOG:
                if await session.get(MarketplaceListing, row["id"]) is None:
                    session.add(MarketplaceListing(**row))
            if await session.get(Business, BIZ) is None:
                session.add(Business(id=BIZ, name="E2E Hiring Co"))
            user_id = None
            existing = (
                await session.execute(select(User).where(User.email == EMAIL))
            ).scalar_one_or_none()
            if existing is None:
                user = User(
                    id="u_e2e",
                    email=EMAIL,
                    password_hash=hash_password(PASSWORD),
                    display_name="E2E",
                )
                session.add(user)
                user_id = user.id
            else:
                user_id = existing.id
            link = (
                await session.execute(
                    select(Membership).where(
                        Membership.user_id == user_id, Membership.business_id == BIZ
                    )
                )
            ).scalar_one_or_none()
            if link is None:
                session.add(Membership(user_id=user_id, business_id=BIZ, role="owner"))
            await _reset_workflow_graph(session)
    await engine.dispose()
    print(f"e2e env ready: business={BIZ} email={EMAIL}")


async def _reset_workflow_graph(session) -> None:
    """Rebuild the deterministic core-workflow graph (one flush per write).

    Delete order respects FKs (approvals/tasks before projects, briefs and
    agents last); rebuild order respects references (agents/brief before the
    plan graph). Brief transitions use the production CAS methods, so only
    legal lifecycle states are reachable here.
    """
    await session.execute(
        delete(PackInstallationItem).where(PackInstallationItem.business_id == BIZ)
    )
    await session.execute(
        delete(PackInstallation).where(PackInstallation.business_id == BIZ)
    )
    await session.execute(delete(Approval).where(Approval.business_id == BIZ))
    await session.execute(delete(Task).where(Task.business_id == BIZ))
    await session.execute(delete(Project).where(Project.business_id == BIZ))
    await session.execute(delete(Brief).where(Brief.business_id == BIZ))
    await session.execute(delete(BrainEntry).where(BrainEntry.business_id == BIZ))
    await session.execute(delete(Agent).where(Agent.business_id == BIZ))
    await session.flush()

    agents = AgentRepository()
    briefs = BriefRepository()
    await agents.create(
        session,
        BIZ,
        {"id": AGENT_ID, "name": "E2E Engineer", "role": "Engineer", "status": "available"},
    )
    await briefs.create(session, BIZ, {"id": BRIEF_ID, "goal": "Ship the E2E workflow slice."})
    await briefs.update(
        session,
        BIZ,
        BRIEF_ID,
        {
            "status": "questions_ready",
            "questions": [{"id": "q1", "text": "What ships first?"}],
        },
    )
    if not await briefs.save_answers(
        session,
        BIZ,
        BRIEF_ID,
        answers=[{"question_id": "q1", "answer": "The workflow slice."}],
        summary={"scope": ["workflow"]},
    ):
        raise RuntimeError("fixture brief answers transition failed")

    projects = ProjectRepository()
    tasks = TaskRepository()
    project, made_tasks, _approvals = await PlanningRepository().create_plan_graph(
        session, BIZ, PLAN
    )
    if not await briefs.mark_planned(session, BIZ, BRIEF_ID, project_id=project.id):
        raise RuntimeError("fixture brief plan transition failed")
    # Docs ship done; deploy stays runnable behind its gate (refuse → approve
    # → run). A separate all-completed project covers the driver terminal.
    # (With a validating provider runs complete; with mock echo they fail —
    # either terminal state is honest, never faked.)
    for task in made_tasks:
        if task.title == "Write workflow docs":
            await tasks.update(session, BIZ, task.id, {"status": "completed", "progress": 100})
    done_project = await projects.create(
        session, BIZ, {"id": "proj_e2e_done", "title": "E2E Done Project"}
    )
    await tasks.create(
        session,
        BIZ,
        {
            "id": "t_e2e_done",
            "project_id": done_project.id,
            "title": "Already done task",
            "owner_agent_id": AGENT_ID,
            "status": "completed",
            "progress": 100,
        },
    )

    runnable = await projects.create(
        session, BIZ, {"id": "proj_e2e_run", "title": "E2E Run Project"}
    )
    await tasks.create(
        session,
        BIZ,
        {
            "id": "t_e2e_run",
            "project_id": runnable.id,
            "title": "Run me honestly",
            "summary": "Mock LLM echo cannot satisfy the result schema.",
            "owner_agent_id": AGENT_ID,
            "status": "not_started",
        },
    )

    recovery = await projects.create(
        session, BIZ, {"id": "proj_e2e_rec", "title": "E2E Recovery Project"}
    )
    await tasks.create(
        session,
        BIZ,
        {
            "id": "t_e2e_stale",
            "project_id": recovery.id,
            "title": "Stuck workflow task",
            "owner_agent_id": AGENT_ID,
            "status": "working",
        },
    )
    # Provably stale: far older than any recovery threshold the server uses.
    await tasks.update(
        session, BIZ, "t_e2e_stale", {"updated_at": datetime(2000, 1, 1, tzinfo=UTC)}
    )


if __name__ == "__main__":
    asyncio.run(main())
