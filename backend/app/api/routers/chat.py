"""POST /api/v1/chat の SSE アダプタ。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from starlette.responses import StreamingResponse

from app.api.auth import get_current_user, get_user_repository
from app.api.schemas.chat import ChatAnswerRequest, ChatEvent, ChatRequest
from app.api.sse import (
    ChatEventBuffer,
    DisconnectAwareGenerationClient,
    iter_sse_frames,
)
from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.llm import GenerationClient
from app.domains.conversation import run_turn
from app.domains.conversation.ask_registry import AskAnswer, AskUserRegistry
from app.domains.conversation.state import TurnState
from app.domains.users import UserData, UserRepository

ChatTurnRunner = Callable[..., Awaitable[TurnState | None]]
logger = logging.getLogger("app.api.chat")


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


class ActiveTurnRegistry:
    """同一プロセス内で 1 ユーザー 1 実行中ターンを守る。

    質問待ち中(`ask_user`)もターンの処理(コルーチン)は生きたままなので、
    `POST /chat` は待機中も 409 のまま(§1.4 の「実行中ターンがある間」)。
    """

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


def get_ask_registry(request: Request) -> AskUserRegistry:
    """`ask_user` の HITL 待ち受けレジストリ(§7)。プロセス内(app.state)に

    1 個だけ持ち、実行中のターン(`ToolAdapters.ask_user`)と
    `POST /chat/answer`・`GET /thread` が同じインスタンスを見る。
    """

    registry = getattr(request.app.state, "ask_registry", None)
    if registry is None:
        registry = AskUserRegistry()
        request.app.state.ask_registry = registry
    return registry


def get_chat_turn_runner() -> ChatTurnRunner:
    return _run_chat_turn


async def _run_chat_turn(
    *,
    user_id: int,
    utterance: str,
    event_sink: ChatEventBuffer,
    settings: Settings,
    disconnected: asyncio.Event,
    ask_registry: AskUserRegistry,
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
            settings=settings,
            llm_client=client,
            ask_registry=ask_registry,
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
    ask_registry: Annotated[AskUserRegistry, Depends(get_ask_registry)],
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
                event_sink=buffer,
                settings=settings,
                disconnected=disconnected,
                ask_registry=ask_registry,
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


@router.post(
    "/chat/answer",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_409_CONFLICT: {"description": "回答を待っているターンがありません"},
    },
)
async def chat_answer(
    body: ChatAnswerRequest,
    current_user: Annotated[UserData, Depends(get_current_user)],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    ask_registry: Annotated[AskUserRegistry, Depends(get_ask_registry)],
) -> Response:
    """`ask_user` への回答(§1.4)。イベントは元の SSE ストリームに流れる。

    `resolves` があればチップ経由(`answered_by:"chip"`)、無ければ自由入力
    (`answered_by:"free_text"`)。回答を待つターンが無ければ 409。

    2026-08-04 レビュー是正(High・裁定5): `resolves` を現在の
    `pending_ask`(`GET /thread` と同じ表示中の質問)と照合する。表示中の
    質問と一致しない(古い質問への `resolves`。例: Q1 の遅延回答が Q2 の
    Future 登録後に届く)場合は 409 で拒否する。一致すれば chip、
    `resolves` 無しは free_text のまま(自由入力は常に受理する)。
    """

    answered_by: Literal["chip", "free_text"] = (
        "chip" if body.resolves is not None else "free_text"
    )
    if body.resolves is not None:
        thread = await repository.get_thread(current_user.id)
        resolves_payload = body.resolves.model_dump()
        if not _resolves_matches_pending(resolves_payload, thread.pending):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="表示中の質問と一致しないため、この回答は受け付けられません",
            )
    answer = AskAnswer(
        answer=body.answer,
        answered_by=answered_by,
        resolves=(body.resolves.model_dump() if body.resolves is not None else None),
    )
    if not ask_registry.resolve(current_user.id, answer):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="回答を待っているターンがありません",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _resolves_matches_pending(
    resolves: Mapping[str, Any], pending: Mapping[str, Any] | None
) -> bool:
    """`resolves`(チップ経由の回答)が、いま表示中の質問と一致するか。

    `pending` は `UserRepository.get_thread` が返す公開形
    (`GET /thread` と同じ。`users/repo.py:_public_pending`)。`kind` は
    preference→`"ask_user"`、clarify→`"clarify"`。
    """

    if pending is None:
        return False
    if "surface" in resolves:  # ClarificationResolutionRequest{surface,value}
        if pending.get("kind") != "clarify":
            return False
        if resolves.get("surface") != pending.get("surface"):
            return False
        options = pending.get("options")
        if not isinstance(options, list):
            return False
        return any(
            isinstance(option, Mapping) and option.get("value") == resolves.get("value")
            for option in options
        )
    if "slot" in resolves:  # AskUserResolutionRequest{slot,value}
        if pending.get("kind") != "ask_user":
            return False
        if resolves.get("slot") != pending.get("slot"):
            return False
        options = pending.get("options")
        if not isinstance(options, list):
            return False
        # `state:ask_user` の options はラベル文字列のみ(§1.2)。フロントは
        # value にラベルそのものを送る(`lib/askAnswer.js`)。
        return resolves.get("value") in options
    return False
