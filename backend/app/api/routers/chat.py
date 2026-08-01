"""POST /api/v1/chat の SSE アダプタ。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import StreamingResponse

from app.api.auth import get_current_user
from app.api.schemas.chat import ChatEvent, ChatRequest
from app.api.sse import (
    ChatEventBuffer,
    DisconnectAwareGenerationClient,
    iter_sse_frames,
)
from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.llm import GenerationClient
from app.domains.conversation import run_turn
from app.domains.conversation.state import TurnState
from app.domains.users import UserData

ChatTurnRunner = Callable[..., Awaitable[TurnState | None]]
logger = logging.getLogger("app.api.chat")


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


class ActiveTurnRegistry:
    """同一プロセス内で 1 ユーザー 1 実行中ターンを守る。"""

    def __init__(self) -> None:
        self._user_ids: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def acquire(self, user_id: int) -> bool:
        if user_id in self._user_ids:
            return False
        self._user_ids.add(user_id)
        return True

    def release(self, user_id: int) -> None:
        self._user_ids.discard(user_id)

    def track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()


router = APIRouter(prefix="/api/v1", tags=["chat"])


def get_active_turn_registry(request: Request) -> ActiveTurnRegistry:
    registry = getattr(request.app.state, "active_turn_registry", None)
    if registry is None:
        registry = ActiveTurnRegistry()
        request.app.state.active_turn_registry = registry
    return registry


def get_chat_turn_runner() -> ChatTurnRunner:
    return _run_chat_turn


async def _run_chat_turn(
    *,
    user_id: int,
    utterance: str,
    resolves: dict[str, str] | None,
    event_sink: ChatEventBuffer,
    settings: Settings,
    disconnected: asyncio.Event,
) -> TurnState:
    client = DisconnectAwareGenerationClient(
        GenerationClient(settings),
        disconnected,
        tool_phase_started=event_sink.tool_phase_started,
    )
    async with session_scope(settings) as session:
        return await run_turn(
            session,
            user_id=user_id,
            utterance=utterance,
            event_sink=event_sink,
            resolves=resolves,
            settings=settings,
            llm_client=client,
        )


@router.post(
    "/chat",
    response_model=ChatEvent,
    response_class=EventStreamResponse,
    responses={
        status.HTTP_409_CONFLICT: {"description": "同じユーザーのターンが実行中"},
    },
)
async def chat(
    body: ChatRequest,
    current_user: Annotated[UserData, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    registry: Annotated[ActiveTurnRegistry, Depends(get_active_turn_registry)],
    runner: Annotated[ChatTurnRunner, Depends(get_chat_turn_runner)],
) -> EventStreamResponse:
    if not registry.acquire(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このユーザーの対話ターンは実行中です",
        )

    fallback_turn_id = str(uuid4())
    buffer = ChatEventBuffer(fallback_turn_id=fallback_turn_id)
    disconnected = asyncio.Event()

    async def execute() -> None:
        state: TurnState | None = None
        try:
            state = await runner(
                user_id=current_user.id,
                utterance=body.message,
                resolves=(body.resolves.model_dump() if body.resolves is not None else None),
                event_sink=buffer,
                settings=settings,
                disconnected=disconnected,
            )
        except asyncio.CancelledError:
            # 切断を受けた N5 は run_turn 内で persist 済み。
            pass
        except Exception:  # noqa: BLE001 - ヘッダ送出後の単一エラー出口
            logger.exception(
                "chat_turn_failed",
                extra={"user_id": current_user.id},
            )
            buffer.emit_stream_failure()
        finally:
            try:
                buffer.finish(
                    turn_id=state.turn_id if state is not None else fallback_turn_id,
                    degraded=bool(state.degraded) if state is not None else False,
                )
            finally:
                registry.release(current_user.id)

    task = asyncio.create_task(execute())
    registry.track(task)

    async def stream_body() -> AsyncIterator[bytes]:
        try:
            async for frame in iter_sse_frames(
                buffer.queue,
                heartbeat_sec=settings.chat_sse_heartbeat_sec,
            ):
                yield frame
        finally:
            # StreamingResponse が http.disconnect で iterator を cancel する。
            # worker task 自体は registry が保持し、最後まで実行する。
            disconnected.set()

    return EventStreamResponse(
        stream_body(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
