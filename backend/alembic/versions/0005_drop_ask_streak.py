"""threads.ask_streak を削除する（ask_user の積極化。旧 A2 の廃止）。

`Docs/adr/0024-ask-user-proactive-hitl.md` の決定: 質問を含むターンの連続
制限（A2）を廃止したため、そのカウンタだった `ask_streak` 列は不要になった。

Revision ID: 0005_drop_ask_streak
Revises: 0004_react_history_profile
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_drop_ask_streak"
down_revision: str | None = "0004_react_history_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("threads", "ask_streak", schema="app")


def downgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "ask_streak",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="app",
    )
