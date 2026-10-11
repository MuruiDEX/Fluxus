"""Versioned API router — all domain routes mount here under /api/v1."""

from fastapi import APIRouter

from app.api.v1 import approvals, auth, briefs, executions, health, reads, recovery, tasks

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(health.router, tags=["health"])
v1_router.include_router(auth.router, tags=["auth"])
v1_router.include_router(approvals.router, tags=["approvals"])
v1_router.include_router(briefs.router, tags=["briefs"])
v1_router.include_router(executions.router, tags=["executions"])
v1_router.include_router(tasks.router, tags=["tasks"])
v1_router.include_router(recovery.router, tags=["recovery"])
v1_router.include_router(reads.router, tags=["reads"])
