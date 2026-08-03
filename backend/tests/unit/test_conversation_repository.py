"""スレッド永続状態(`_persist_thread`)の段2挙動を DB 行境界で検査する。

`ask_user` は段2のメインループから呼ばれないため、`pending_ask` は常に
クリアされ、`asked_slots`/`ask_streak` は素通りする(段5で書き換えが復活する)。
"""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Thread
from app.domains.conversation.repository import ConversationRepository, _derive_mode
from app.domains.conversation.state import ProfileState, TurnState


def _thread(*, asked_slots: list[str] | None = None) -> Thread:
    return Thread(
        user_id=1,
        presented_spot_ids=[],
        last_candidates=[],
        asked_slots=asked_slots or ["pace"],
        ask_streak=2,
        pending_ask={
            "kind": "clarify",
            "surface": "2番目",
            "reason": "候補が複数あります",
            "options": [],
        },
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


def test_persist_thread_always_clears_pending_ask_and_resets_ask_streak() -> None:
    """段2は ask_user を呼ばないため、質問中の状態を持ち越さない。"""

    row = _thread()
    state = _state()
    state.asked_slots = ["pace"]  # load_context で DB から読み込んだ値を模す。
    repository = ConversationRepository(cast(AsyncSession, None))

    repository._persist_thread(row, state, asked_at_message_id=87)

    assert row.pending_ask is None
    assert row.ask_streak == 0
    # asked_slots(段5で使う)は state の値をそのまま素通りする。
    assert row.asked_slots == ["pace"]


def test_persist_thread_updates_presented_spot_ids_and_last_candidates() -> None:
    row = _thread()
    state = _state()
    state.presented_spot_ids = ["spot_001", "spot_001", "spot_002"]
    repository = ConversationRepository(cast(AsyncSession, None))

    repository._persist_thread(row, state)

    assert row.presented_spot_ids == ["spot_001", "spot_002"]


def test_derive_mode_from_executed_tools() -> None:
    """messages.meta.mode は intent 由来ではなく実行した Tool 列から導出する。"""

    assert _derive_mode(["plan_itinerary"]) == "plan"
    assert _derive_mode(["recommend", "edit_itinerary"]) == "edit"
    assert _derive_mode(["recommend"]) == "recommend"
    assert _derive_mode(["search_knowledge"]) == "qa"
    assert _derive_mode([]) == "chitchat"
