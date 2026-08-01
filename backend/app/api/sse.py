"""conversation 内部イベントを検証済み SSE フレームへ変換する。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from typing import Any

from pydantic import BaseModel

from app.api.schemas.chat import ChatEvent, DoneChatEvent, ErrorChatEvent
from app.domains.conversation.events import ConversationEvent

HEARTBEAT_FRAME = b": keep-alive\n\n"
_STREAM_END = object()


def adapt_conversation_event(
    event: ConversationEvent,
    *,
    spot_names: Mapping[str, str] | None = None,
) -> ChatEvent:
    """内部名を API 契約へ写像し、全イベントを Pydantic で検証する。"""

    payload = dict(event.data)
    if event.event == "state":
        kind = payload.get("kind")
        if kind == "candidates":
            payload = _candidate_payload(payload, spot_names or {})
        elif kind == "itinerary" and "phase" not in payload and "stage" in payload:
            payload["phase"] = payload.pop("stage")
        elif kind == "plan":
            payload["steps"] = [
                {"id": step.get("id"), "tool": step.get("tool")}
                for raw in payload.get("steps", [])
                if isinstance((step := _mapping(raw)), dict)
            ]
        elif kind == "clarify":
            payload["options"] = [
                _clarification_option(raw)
                for raw in payload.get("options", [])
                if _mapping(raw) is not None
            ]
    return ChatEvent.model_validate({"event": event.event, "data": payload})


def frame_sse(event: ChatEvent) -> bytes:
    """`event:` / `data:` / 空行からなる 1 SSE イベントを作る。"""

    value = event.root
    data = value.data.model_dump_json()
    return f"event: {value.event}\ndata: {data}\n\n".encode()


async def iter_sse_frames(
    queue: asyncio.Queue[ChatEvent | object],
    *,
    heartbeat_sec: float,
) -> AsyncIterator[bytes]:
    """token が来ない区間へ、指定間隔でコメント行を挿入する。"""

    loop = asyncio.get_running_loop()
    next_heartbeat = loop.time() + heartbeat_sec
    while True:
        remaining = next_heartbeat - loop.time()
        if remaining <= 0:
            yield HEARTBEAT_FRAME
            next_heartbeat = loop.time() + heartbeat_sec
            continue
        try:
            item = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            yield HEARTBEAT_FRAME
            next_heartbeat = loop.time() + heartbeat_sec
            continue
        if item is _STREAM_END:
            return
        if not isinstance(item, ChatEvent):  # pragma: no cover - queue の型境界
            raise TypeError("SSE queue に未検証の値が入りました")
        yield frame_sse(item)
        if item.root.event == "token":
            next_heartbeat = loop.time() + heartbeat_sec


class ChatEventBuffer:
    """`done` を完走時まで保留し、最後に一度だけ queue へ置く。"""

    def __init__(self, *, fallback_turn_id: str) -> None:
        self.queue: asyncio.Queue[ChatEvent | object] = asyncio.Queue()
        self.fallback_turn_id = fallback_turn_id
        self.tool_phase_started = asyncio.Event()
        self._done: ChatEvent | None = None
        self._finished = False

    async def emit(self, event: ConversationEvent) -> None:
        adapted = adapt_conversation_event(event)
        if adapted.root.event == "state" and adapted.root.data.kind in {
            "plan",
            "candidates",
            "itinerary",
            "ask_user",
            "searching",
        }:
            self.tool_phase_started.set()
        if adapted.root.event == "done":
            if self._done is None:
                self._done = adapted
            return
        if not self._finished:
            self.queue.put_nowait(adapted)

    def emit_stream_failure(self) -> None:
        if self._finished:
            return
        self.queue.put_nowait(
            ChatEvent.model_validate(
                {
                    "event": "error",
                    "data": {
                        "stage": "persist",
                        "code": "stream_failed",
                        "degraded": False,
                        "message": "対話処理中に予期しない問題が発生しました",
                    },
                }
            )
        )

    def finish(
        self,
        *,
        turn_id: str | None = None,
        degraded: bool = False,
    ) -> None:
        if self._finished:
            return
        done = self._done or ChatEvent.model_validate(
            {
                "event": "done",
                "data": {
                    "turn_id": turn_id or self.fallback_turn_id,
                    "message_id": None,
                    "degraded": degraded,
                },
            }
        )
        self.queue.put_nowait(done)
        self.queue.put_nowait(_STREAM_END)
        self._finished = True


class DisconnectAwareGenerationClient:
    """切断時に understand / respond の LLM 呼び出しだけを閉じる proxy。"""

    def __init__(
        self,
        client: Any,
        disconnected: asyncio.Event,
        *,
        tool_phase_started: asyncio.Event | None = None,
    ) -> None:
        self.client = client
        self.disconnected = disconnected
        self.tool_phase_started = tool_phase_started or asyncio.Event()

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        operation = self.client.generate(messages, **kwargs)
        if self.tool_phase_started.is_set():
            # リランク・解選択は Tool の一部なので切断後も完了させる。
            return await operation
        return await self._until_disconnect(operation)

    async def stream(
        self,
        messages: Sequence[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        iterator = self.client.stream(messages, **kwargs).__aiter__()
        disconnected = asyncio.create_task(self.disconnected.wait())
        next_chunk: asyncio.Task[str] | None = None
        try:
            while True:
                next_chunk = asyncio.create_task(anext(iterator))
                done, _ = await asyncio.wait(
                    {next_chunk, disconnected},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnected in done:
                    next_chunk.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_chunk
                    raise asyncio.CancelledError
                try:
                    yield next_chunk.result()
                except StopAsyncIteration:
                    return
        finally:
            if next_chunk is not None and not next_chunk.done():
                next_chunk.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_chunk
            disconnected.cancel()
            with suppress(asyncio.CancelledError):
                await disconnected
            close = getattr(iterator, "aclose", None)
            if close is not None:
                with suppress(asyncio.CancelledError, RuntimeError):
                    await close()

    async def _until_disconnect(self, operation: Any) -> Any:
        running = asyncio.create_task(operation)
        disconnected = asyncio.create_task(self.disconnected.wait())
        try:
            done, _ = await asyncio.wait(
                {running, disconnected},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnected in done:
                running.cancel()
                with suppress(asyncio.CancelledError):
                    await running
                raise asyncio.CancelledError
            return running.result()
        finally:
            disconnected.cancel()
            with suppress(asyncio.CancelledError):
                await disconnected


def is_done_event(event: ChatEvent) -> bool:
    return isinstance(event.root, DoneChatEvent)


def is_error_event(event: ChatEvent) -> bool:
    return isinstance(event.root, ErrorChatEvent)


def _candidate_payload(
    payload: dict[str, Any],
    spot_names: Mapping[str, str],
) -> dict[str, Any]:
    phase = payload.get("phase", payload.get("stage"))
    raw_items = payload.get("items", payload.get("candidates", []))
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        candidate = _mapping(raw)
        if candidate is None:
            continue
        spot_id = candidate.get("spot_id")
        if not isinstance(spot_id, str):
            continue
        reason = candidate.get("reason_materials", {})
        reason_mapping = _mapping(reason) or {}
        name = candidate.get("name_ja")
        items.append(
            {
                "spot_id": spot_id,
                "name_ja": name if isinstance(name, str) else spot_names.get(spot_id, spot_id),
                "reason_materials": reason_mapping,
            }
        )
    return {"kind": "candidates", "phase": phase, "items": items}


def _clarification_option(raw: Any) -> dict[str, Any]:
    option = _mapping(raw) or {}
    value = option.get("value")
    if not isinstance(value, str):
        resolution = _mapping(option.get("resolves_to")) or {}
        value = resolution.get("value")
    return {"label": option.get("label"), "value": value}


def _mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return None
