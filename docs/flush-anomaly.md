# SQLAlchemy flush() anomaly — isolated report (Stage 2 finding, Stage 3 evidence)

Status: **reproduced, root cause NOT isolated.** Nothing below is presented
as a proven cause. This document records only observed behavior so Stage 4
can navigate it safely.

## Observed behavior

A single `session.flush()` containing FK-related new rows across two tables
emits **only the child INSERT** — the parent INSERT is never attempted —
and the transaction fails with `ForeignKeyViolationError`:

```text
BEGIN (implicit)
INSERT INTO repro_child (id, pid) VALUES ($1::VARCHAR, $2::VARCHAR)
→ ForeignKeyViolationError
ROLLBACK
```

The parent row (`P(id='p1')`, confirmed present in `session.new` alongside
the child at flush time) is silently skipped by the unit of work. No error
is raised for the missing parent; the failure surfaces only as the child's
FK violation.

## Minimal reproduction

`backend/scripts/repro_flush_anomaly.py` (diagnostic only, not product code):

```python
s.add(P(id="p1"))
s.add(C(id="c1", pid="p1"))   # C.pid → ForeignKey("P.id")
await s.flush()               # emits ONLY INSERT INTO child → IntegrityError
```

Actual output on the current stack (2026-10-09):

```text
postgres: single_flush=True -> IntegrityError
postgres: single_flush=False -> OK
SUMMARY postgres single-flush=IntegrityError sequential=OK
```

Sequential flushes (parent flushed before the child is added) always succeed.

## Tested matrix (facts only)

| Dimension | Tested |
|---|---|
| SQLAlchemy 2.1.4 + asyncpg 0.32.0 + PG 16.15 (embedded) | single-flush FAILS, sequential OK |
| SQLite (aiosqlite, same code) | single-flush OK |
| Raw Core inserts (no ORM) on PG | OK |

NOT tested (no claim made): other SQLAlchemy/asyncpg/PG versions, sync
drivers, other platforms. The defect was not bisected to library vs driver
vs server, and no hypothesis is asserted.

## Constraints for Stage 4 (binding until disproven otherwise)

1. **One aggregate per flush.** Every repository `create()` flushes exactly
   the row(s) of its own aggregate before related rows are added — this is
   already the codebase-wide pattern (`repositories/concrete.py`,
   `scripts/seed_demo.py` flushes incrementally) and is covered by
   `tests/test_world_flow.py` (full world built through public repo APIs
   in one transaction — green).
2. **No multi-table single flush** of FK-related new rows anywhere in new code.
3. **No masking**: no `try/except` around flushes that could hide a skipped
   parent; no synthetic pre-inserts; no `use_alter`-style DDL games to work
   around runtime behavior.
4. If Stage 4 needs batched writes, it must first extend
   `scripts/repro_flush_anomaly.py` with the exact batch shape and prove it
   green on the Stage 4 stack before relying on it.
