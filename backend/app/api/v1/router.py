"""Versioned API router — all domain routes mount here under /api/v1."""

from fastapi import APIRouter

from app.api.v1 import (
    agents,
    approvals,
    auth,
    briefs,
    executions,
    health,
    marketplace,
    packs,
    reads,
    recovery,
    tasks,
    usage,
    writes,
)

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(health.router, tags=["health"])
v1_router.include_router(auth.router, tags=["auth"])
v1_router.include_router(agents.router, tags=["agents"])
v1_router.include_router(approvals.router, tags=["approvals"])
v1_router.include_router(briefs.router, tags=["briefs"])
v1_router.include_router(executions.router, tags=["executions"])
v1_router.include_router(tasks.router, tags=["tasks"])
v1_router.include_router(recovery.router, tags=["recovery"])
v1_router.include_router(reads.router, tags=["reads"])
v1_router.include_router(usage.router, tags=["usage"])
v1_router.include_router(marketplace.router, tags=["marketplace"])
v1_router.include_router(packs.router, tags=["packs"])
v1_router.include_router(writes.router, tags=["writes"])
