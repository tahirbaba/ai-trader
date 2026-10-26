"""trades.symbol (Phase 9 dashboard markers)

Revision ID: a9f1c0d2e3b4
Revises: 29765e23d35f
Create Date: 2026-10-27 00:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9f1c0d2e3b4"
down_revision: str | None = "29765e23d35f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("trades") as batch:
        batch.add_column(sa.Column("symbol", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trades") as batch:
        batch.drop_column("symbol")
