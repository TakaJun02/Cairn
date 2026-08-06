"""チャット SSE 契約へ接続する、送出先に依存しないイベント抽象。"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol, TypeAlias, get_args

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("app.conversation.events")


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


ErrorStage = Literal[
    "load_context",
    "update_profile",
    "main_agent",
    "recommend",
    "plan_itinerary",
    "edit_itinerary",
    "search_knowledge",
    "respond",
    "persist",
]

_VALID_ERROR_STAGES: frozenset[str] = frozenset(get_args(ErrorStage))
_FALLBACK_ERROR_STAGE: ErrorStage = "main_agent"


def resolve_error_stage(candidate: str) -> str:
    """`candidate` を `ErrorStage` の語彙に正規化する小ヘルパ。

    2026-08-04、レビュー是正(M-1): `main_agent.py` のようにループ打ち切り時
    「失敗した Tool 名を stage に使いたいが、Tool 名の一部(`ask_user`)は
    語彙に無い」場面で使う。語彙集合(`_VALID_ERROR_STAGES`)は
    `error_event` と共有し、ここで二重定義しない。語彙内ならそのまま返し
    (Tool 名を保つ)、語彙外なら `main_agent` へフォールバックする。
    """

    return candidate if candidate in _VALID_ERROR_STAGES else _FALLBACK_ERROR_STAGE


def error_event(
    *,
    stage: str,
    code: str,
    degraded: bool,
    message: str,
) -> ConversationEvent:
    """`error` イベントを組み立てる。

    `stage` は API 契約(`ErrorStage`)の語彙で検証する(2026-08-04、レビュー
    是正: Critical)。旧実装は `"act"` のような契約外の値をそのまま
    `ChatEventBuffer.emit` まで運び、Pydantic 検証で例外を投げていた
    ([chat_sse.md §1.2](../../../../Docs/40_api/chat_sse.md))。ここで**構築時に**
    検証し、不正なら安全側(`main_agent`)へ落としてログするため、Tool の
    結果適用の途中で例外が伝播して不変条件(persist に必ず到達)を壊すことが
    なくなる。
    """

    if stage not in _VALID_ERROR_STAGES:
        # `extra` のキーは LogRecord の予約名(`message`/`asctime` 等)を避ける
        # (2026-08-04 実機再現、[25 §1-6])。`message` を使うと
        # `Logger.makeRecord` が `KeyError("Attempt to overwrite 'message' in
        # LogRecord")` を投げ、この防御自体がターンを落としていた。
        logger.warning(
            "invalid_error_stage",
            extra={"stage": stage, "code": code, "error_message": message},
        )
        stage = _FALLBACK_ERROR_STAGE
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
