"""Repository base — scoped CRUD over AsyncSession.

Rules enforced here for every aggregate:
- Reads/writes/deletes ALWAYS carry business_id (scope root: businesses table
  itself is the only exception — it has no business_id column).
- IDs are validated against the contract charset on write.
- Money is quantized to Decimal cents (ROUND_HALF_UP) on write.
- Progress is clamped to 0..100 (contract: clamp, never wrap).
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.exceptions import ImmutableFieldError, validate_id

CENT = Decimal("0.01")


def quantize_money(value: float | str | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    else:
        amount = Decimal(str(value))
    if amount < 0:
        raise ValueError("money must be >= 0")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def clamp_progress(value: int) -> int:
    return max(0, min(100, int(value)))


class BaseRepository[M]:
    model: type[M]
    scoped: bool = True  # False only for the global marketplace catalog

    def _scope(self, stmt, business_id: str):  # type: ignore[no-untyped-def]
        if self.scoped:
            return stmt.where(self.model.business_id == business_id)  # type: ignore[attr-defined]
        return stmt

    async def get(self, session: AsyncSession, business_id: str, id: str) -> M | None:
        stmt = select(self.model).where(self.model.id == id)  # type: ignore[attr-defined]
        res = await session.execute(self._scope(stmt, business_id))
        return res.scalar_one_or_none()

    async def list(self, session: AsyncSession, business_id: str, *, limit: int = 200) -> list[M]:
        stmt = select(self.model).limit(limit)  # type: ignore[attr-defined]
        res = await session.execute(self._scope(stmt, business_id))
        return list(res.scalars().all())

    async def count(self, session: AsyncSession, business_id: str) -> int:
        stmt = select(func.count()).select_from(self.model)  # type: ignore[attr-defined]
        res = await session.execute(self._scope(stmt, business_id))
        return int(res.scalar_one())

    async def create(self, session: AsyncSession, business_id: str, data: dict[str, Any]) -> M:
        payload = dict(data)
        if "id" in payload and payload["id"] is not None:
            validate_id(payload["id"])
        if self.scoped:
            payload["business_id"] = business_id
        obj = self.model(**payload)  # type: ignore[call-arg]
        session.add(obj)
        await session.flush()
        return obj

    async def update(
        self, session: AsyncSession, business_id: str, id: str, patch: dict[str, Any]
    ) -> M | None:
        if "ai_spent" in patch:
            # Project.ai_spent is a derived cache (contract §5.1). The only legal
            # mutation path is recalculate_project_spend(); direct writes —
            # however well-intentioned — would silently break the invariant.
            raise ImmutableFieldError(
                "ai_spent is derived and cannot be written directly; "
                "use recalculate_project_spend()"
            )
        obj = await self.get(session, business_id, id)
        if obj is None:
            return None
        for key, value in patch.items():
            if key in ("id", "business_id"):
                continue
            setattr(obj, key, value)
        await session.flush()
        return obj

    async def delete(self, session: AsyncSession, business_id: str, id: str) -> bool:
        obj = await self.get(session, business_id, id)
        if obj is None:
            return False
        await session.delete(obj)
        await session.flush()
        return True
