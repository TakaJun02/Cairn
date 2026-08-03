"""ask_user のスレッド永続状態を DB 行境界で検査する。"""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Thread
from app.domains.conversation.repository import ConversationRepository
from app.domains.conversation.state import ProfileState, TurnState


def _thread(*, pending_ask: dict[str, Any] | None = None) -> Thread:
    return Thread(
        user_id=1,
        presented_spot_ids=[],
        last_candidates=[],
        asked_slots=[],
        ask_streak=1,
        pending_ask=pending_ask,
        resolved_ambiguities=[],
        pending_constraints=[],
    )


def _state() -> TurnState:
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="鶴間池",
        profile=ProfileState(),
    )


def test_persist_sets_pending_ask_for_suspended_turn() -> None:
    row = _thread()
    state = _state()
    state.should_end_turn = True
    state.pending_ask = {
        "kind": "preference",
        "slot": "pace",
        "reason": "希望のペースを確認します",
        "options": [
            {"label": "ゆったり", "value": "relaxed"},
            {"label": "多め", "value": "packed"},
        ],
    }
    repository = ConversationRepository(cast(AsyncSession, None))

    repository._persist_thread(row, state, asked_at_message_id=87)

    assert row.ask_streak == 1
    assert row.asked_slots == ["pace"]
    assert row.pending_ask == {
        **state.pending_ask,
        "original_utterance": "鶴間池",
        "asked_at_message_id": 87,
    }


def test_persist_expires_previous_pending_after_exactly_one_input_turn() -> None:
    row = _thread(
        pending_ask={
            "kind": "clarify",
            "surface": "2番目",
            "reason": "候補が複数あります",
            "options": [
                {"label": "鶴間池", "value": "spot_001"},
                {"label": "元滝伏流水", "value": "spot_002"},
            ],
        }
    )
    state = _state()
    state.tool_results = [
        {
            "tool": "ask_user",
            "input": {"kind": "clarify", "surface": "2番目"},
            "output": {
                "answer": "spot_001",
                "answered_by": "chip",
                "surface": "2番目",
            },
        }
    ]
    repository = ConversationRepository(cast(AsyncSession, None))

    repository._persist_thread(row, state)

    assert row.pending_ask is None
    assert row.ask_streak == 0
    assert row.resolved_ambiguities == [
        {"surface": "2番目", "resolved_to": "spot_001"}
    ]
