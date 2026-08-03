"""threads へ会話履歴要約の列を追加する（ReAct 構成 段1）。

Revision ID: 0004_react_history_profile
Revises: 0003_ask_user_resumable_tool
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_react_history_profile"
down_revision: str | None = "0003_ask_user_resumable_tool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "history_summary",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        schema="app",
    )
    op.add_column(
        "threads",
        sa.Column("summarized_until_message_id", sa.BigInteger(), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("threads", "summarized_until_message_id", schema="app")
    op.drop_column("threads", "history_summary", schema="app")
