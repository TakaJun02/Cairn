"""N6 `persist`: respond の成否に関係なく通る 1 トランザクション境界。

`done` 送出後に履歴要約の畳み込み更新（history_summary.py）を走らせる
（§8）。応答のクリティカルパスには載らない位置であり、失敗しても
このターンの成否には影響しない。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.domains.conversation.events import EventSinkLike, done_event, emit
from app.domains.conversation.history_summary import update_history_summary
from app.domains.conversation.state import TurnState


class PersistRepositoryPort(Protocol):
    async def persist_turn(self, state: TurnState) -> int | None: ...


async def persist(
    state: TurnState,
    *,
    repository: PersistRepositoryPort,
    event_sink: EventSinkLike = None,
    history_client: Any | None = None,
) -> int | None:
    message_id = await repository.persist_turn(state)
    await emit(
        event_sink,
        done_event(
            turn_id=state.turn_id,
            message_id=message_id,
            degraded=bool(state.degraded),
        ),
    )
    if hasattr(repository, "load_history_summary_state") and hasattr(
        repository, "commit_history_summary"
    ):
        await update_history_summary(
            state,
            repository=repository,  # type: ignore[arg-type]
            client=history_client,
        )
    return message_id
