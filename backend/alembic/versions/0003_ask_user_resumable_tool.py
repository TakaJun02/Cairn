"""ask_user の中断状態と連続回数を 1 系統へ統合する。

Revision ID: 0003_ask_user_resumable_tool
Revises: 0002_realtime_simulator_state
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_ask_user_resumable_tool"
down_revision: str | None = "0002_realtime_simulator_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "threads",
        "pending_clarification",
        new_column_name="pending_ask",
        schema="app",
    )
    op.execute(
        """
        UPDATE app.threads
        SET pending_ask = jsonb_strip_nulls(
              jsonb_build_object(
                'kind', 'clarify',
                'surface', pending_ask->>'surface',
                'slot', NULL,
                'reason', COALESCE(
                  pending_ask->>'reason',
                  pending_ask->>'why',
                  ''
                ),
                'options', COALESCE(
                  (
                    SELECT jsonb_agg(
                      jsonb_build_object(
                        'label', option->>'label',
                        'value', COALESCE(
                          option->>'value',
                          option#>>'{resolves_to,value}'
                        )
                      )
                    )
                    FROM jsonb_array_elements(
                      COALESCE(pending_ask->'options', '[]'::jsonb)
                    ) AS option
                  ),
                  '[]'::jsonb
                ),
                'original_utterance', COALESCE(
                  pending_ask->>'original_utterance',
                  pending_ask->>'utterance'
                ),
                'asked_at_message_id', pending_ask->'asked_at_message_id'
              )
            )
        WHERE pending_ask IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE app.threads
        SET ask_streak = GREATEST(ask_streak, clarify_streak)
        """
    )
    op.drop_column("threads", "clarify_streak", schema="app")


def downgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "clarify_streak",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema="app",
    )
    op.execute(
        """
        UPDATE app.threads
        SET clarify_streak = CASE
              WHEN pending_ask->>'kind' = 'clarify' THEN ask_streak
              ELSE 0
            END,
            pending_ask = CASE
              WHEN pending_ask->>'kind' = 'clarify' THEN jsonb_strip_nulls(
                jsonb_build_object(
                  'surface', pending_ask->>'surface',
                  'why', pending_ask->>'reason',
                  'options', COALESCE(
                    (
                      SELECT jsonb_agg(
                        jsonb_build_object(
                          'label', option->>'label',
                          'resolves_to', jsonb_build_object(
                            'kind', CASE
                              WHEN option->>'value' IN (
                                'all_matches',
                                'single_match',
                                'current_itinerary',
                                'last_candidates'
                              ) THEN 'interpretation'
                              ELSE 'spot_id'
                            END,
                            'value', option->>'value'
                          )
                        )
                      )
                      FROM jsonb_array_elements(
                        COALESCE(pending_ask->'options', '[]'::jsonb)
                      ) AS option
                    ),
                    '[]'::jsonb
                  ),
                  'utterance', pending_ask->>'original_utterance'
                )
              )
              ELSE NULL
            END
        """
    )
    op.alter_column(
        "threads",
        "pending_ask",
        new_column_name="pending_clarification",
        schema="app",
    )
