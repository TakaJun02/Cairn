"""チャット SSE 契約へ接続する、送出先に依存しないイベント抽象。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class ConversationEvent(BaseModel):
    """SSE の `event:` と JSON `data:` を分けた内部表現。"""

    model_config = ConfigDict(extra="forbid")

    event: Literal["state", "token", "error", "done"]
    data: dict[str, Any] = Field(default_factory=dict)


class EventSink(Protocol):
    async def emit(self, event: ConversationEvent) -> None: ...


EventSinkLike: TypeAlias = (
    EventSink | Callable[[ConversationEvent], Awaitable[None] | None] | None
)


class MemoryEventSink:
    """層 1 テストと実 LLM 確認でイベント列を検査するシンク。"""

    def __init__(self) -> None:
        self.events: list[ConversationEvent] = []

    async def emit(self, event: ConversationEvent) -> None:
        self.events.append(event.model_copy(deep=True))

    @property
    def sequence(self) -> list[str]:
        return [
            f"{event.event}:{event.data.get('kind', event.data.get('code', ''))}".rstrip(":")
            for event in self.events
        ]


async def emit(sink: EventSinkLike, event: ConversationEvent) -> None:
    """オブジェクトと callable の双方をテスト差し替えに使う。"""

    if sink is None:
        return
    target = sink.emit if hasattr(sink, "emit") else sink
    result = target(event)  # type: ignore[misc,operator]
    if inspect.isawaitable(result):
        await result


def state_event(kind: str, **payload: Any) -> ConversationEvent:
    return ConversationEvent(event="state", data={"kind": kind, **payload})


def token_event(text: str) -> ConversationEvent:
    return ConversationEvent(event="token", data={"text": text})


def error_event(
    *,
    stage: Literal["understand", "validate_plan", "act", "respond", "persist"],
    code: str,
    degraded: bool,
    message: str,
) -> ConversationEvent:
    return ConversationEvent(
        event="error",
        data={
            "stage": stage,
            "code": code,
            "degraded": degraded,
            "message": message,
        },
    )


def done_event(
    *, turn_id: str, message_id: int | None, degraded: bool
) -> ConversationEvent:
    return ConversationEvent(
        event="done",
        data={"turn_id": turn_id, "message_id": message_id, "degraded": degraded},
    )
