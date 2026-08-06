"""シミュレータのシナリオと進行状態を DB に保持する。

Revision ID: 0002_realtime_simulator_state
Revises: 0001_initial_schema
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_realtime_simulator_state"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "realtime_simulator_state",
        sa.Column("singleton_id", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("scenario", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("running", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("speed", sa.REAL(), server_default=sa.text("1"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elapsed_min", sa.REAL(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_event_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("singleton_id = 1"),
        sa.CheckConstraint("speed > 0"),
        sa.CheckConstraint("elapsed_min >= 0"),
        sa.CheckConstraint("next_event_index >= 0"),
        sa.PrimaryKeyConstraint("singleton_id"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("realtime_simulator_state", schema="app")
