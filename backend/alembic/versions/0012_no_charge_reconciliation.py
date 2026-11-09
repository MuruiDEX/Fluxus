"""0012 attested no-charge reconciliation outcome.

Revision ID: 0012_no_charge_reconciliation
Revises: 0011_usage_reconciliation
Create Date: 2026-11-08

Stage 18.1 F-I1 corrective:

- Extend the `usage_reconciliations` audit table with a `no_charge` outcome.
  A no-charge row is an explicit owner attestation that the provider did not
  bill the attempt. It has no linked usage row, zero amount and cost source
  `none`; the reservation transitions `ambiguous → released` with reason
  `manual_no_charge`.
- Make `usage_record_id` nullable. Existing `charged` rows keep their link and
  all existing constraints are preserved semantically.
- Replace the unconditional charged-only CHECKs with conditional CHECKs so a
  charged row still requires a positive provider amount and a usage link, while
  a no-charge row requires amount zero, cost source `none` and no usage link.
- Add a conditional usage-record CHECK. The existing UNIQUE reservation and
  UNIQUE usage-record constraints are untouched; PostgreSQL permits multiple
  NULL usage-record links.

Downgrade refuses to run while any `no_charge` audit row or any
`released`/`manual_no_charge` reservation exists: reverting the outcome
constraint would either fail on those rows or force silently discarding an
attested resolution. Resolve or export them first. Charged audit rows are
preserved and remain valid under the restored charged-only CHECKs.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_no_charge_reconciliation"
down_revision: str | None = "0011_usage_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "usage_reconciliations",
        "usage_record_id",
        existing_type=sa.String(128),
        nullable=True,
    )

    op.drop_constraint(
        "ck_usage_reconciliations_outcome",
        "usage_reconciliations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_usage_reconciliations_outcome",
        "usage_reconciliations",
        "outcome IN ('charged', 'no_charge')",
    )

    op.drop_constraint(
        "ck_usage_reconciliations_cost_source",
        "usage_reconciliations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_usage_reconciliations_cost_source",
        "usage_reconciliations",
        "(outcome = 'charged' AND cost_source = 'provider') "
        "OR (outcome = 'no_charge' AND cost_source = 'none')",
    )

    op.drop_constraint(
        "ck_usage_reconciliations_amount_pos",
        "usage_reconciliations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_usage_reconciliations_amount",
        "usage_reconciliations",
        "(outcome = 'charged' AND amount > 0) OR (outcome = 'no_charge' AND amount = 0)",
    )

    op.create_check_constraint(
        "ck_usage_reconciliations_usage_record",
        "usage_reconciliations",
        "(outcome = 'charged' AND usage_record_id IS NOT NULL) "
        "OR (outcome = 'no_charge' AND usage_record_id IS NULL)",
    )


def downgrade() -> None:
    no_charge_audit = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM usage_reconciliations WHERE outcome = 'no_charge'")
    )
    if int(no_charge_audit.scalar_one()) > 0:
        raise RuntimeError(
            "Cannot downgrade 0012: no-charge reconciliation audit rows exist. "
            "Export or explicitly resolve them first; a silent downgrade would "
            "discard an attested no-charge resolution."
        )

    no_charge_released = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM budget_reservations "
            "WHERE status = 'released' AND reason = 'manual_no_charge'"
        )
    )
    if int(no_charge_released.scalar_one()) > 0:
        raise RuntimeError(
            "Cannot downgrade 0012: released/manual_no_charge reservations exist. "
            "Export or explicitly resolve them first; a silent downgrade would "
            "discard an attested no-charge resolution."
        )

    op.drop_constraint(
        "ck_usage_reconciliations_usage_record",
        "usage_reconciliations",
        type_="check",
    )
    op.drop_constraint(
        "ck_usage_reconciliations_amount",
        "usage_reconciliations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_usage_reconciliations_amount_pos",
        "usage_reconciliations",
        "amount > 0",
    )

    op.drop_constraint(
        "ck_usage_reconciliations_cost_source",
        "usage_reconciliations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_usage_reconciliations_cost_source",
        "usage_reconciliations",
        "cost_source = 'provider'",
    )

    op.drop_constraint(
        "ck_usage_reconciliations_outcome",
        "usage_reconciliations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_usage_reconciliations_outcome",
        "usage_reconciliations",
        "outcome IN ('charged')",
    )

    op.alter_column(
        "usage_reconciliations",
        "usage_record_id",
        existing_type=sa.String(128),
        nullable=False,
    )
