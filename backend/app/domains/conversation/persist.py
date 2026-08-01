"""N6 `persist`: respond の成否に関係なく通る 1 トランザクション境界。"""

from __future__ import annotations

from typing import Protocol

from app.domains.conversation.events import EventSinkLike, done_event, emit
from app.domains.conversation.state import TurnState


class PersistRepositoryPort(Protocol):
    async def persist_turn(self, state: TurnState) -> int | None: ...


async def persist(
    state: TurnState,
    *,
    repository: PersistRepositoryPort,
    event_sink: EventSinkLike = None,
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
    return message_id
