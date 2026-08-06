"""conversation 内部イベントを検証済み SSE フレームへ変換する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from typing import Any

from pydantic import BaseModel, ValidationError

from app.api.schemas.chat import ChatEvent, DoneChatEvent, ErrorChatEvent
from app.domains.conversation.events import ConversationEvent
from app.domains.conversation.itinerary_digest import UNNAMED_SPOT_JA

HEARTBEAT_FRAME = b": keep-alive\n\n"
_STREAM_END = object()
logger = logging.getLogger("app.api.sse")


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
        elif kind == "clarify":
            payload["options"] = [
                _clarification_option(raw)
                for raw in payload.get("options", [])
                if _mapping(raw) is not None
            ]
    return ChatEvent.model_validate({"event": event.event, "data": payload})


def _safe_adapt(event: ConversationEvent) -> ChatEvent | None:
    """`adapt_conversation_event` を検証エラーから守る安全網(2026-08-04、

    レビュー是正: Critical)。`domains.conversation.events.error_event` が
    `stage` を既に検証しているため通常はここへ到達しないが、想定外の契約
    違反(将来の実装ミス・テストの穴)でも `ChatEventBuffer.emit` が例外を
    投げて Tool の結果適用を中断させない、という不変条件をコードで保証する。
    """

    try:
        return adapt_conversation_event(event)
    except ValidationError as exc:
        logger.warning(
            "chat_event_validation_failed",
            extra={"event": event.event, "data": event.data, "error": str(exc)},
        )
        if event.event != "error":
            # state/token/done は代替できないので、契約違反なら丸ごと落として
            # ターンの完走を優先する(P3: 失敗を正常応答に偽装しないが、
            # ここでは「イベント 1 件を失う」ことと「ターン全体を落とす」
            # ことのどちらかを選ぶ場面であり、後者を避ける)。
            return None
        message = str(event.data.get("message") or "内部でエラーが発生しました")
        return ChatEvent.model_validate(
            {
                "event": "error",
                "data": {
                    "stage": "main_agent",
                    "code": "internal",
                    "degraded": True,
                    "message": message,
                },
            }
        )


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
        adapted = _safe_adapt(event)
        if adapted is None:
            # 契約違反のイベントは、ターンの完走(persist への到達)を優先して
            # 落とす(2026-08-04、レビュー是正: Critical。`_safe_adapt` が
            # 既にログ済み)。`error` イベント自体の変換失敗のみ、安全側の
            # `stage="main_agent"` イベントへ差し替えて必ずクライアントへ
            # 届ける(ユーザーには「縮退した」ことだけは伝える)。
            return
        if adapted.root.event == "state" and adapted.root.data.kind in {
            "step",
            "candidates",
            "itinerary",
            "ask_user",
            "clarify",
        }:
            self.tool_phase_started.set()
            # 2026-08-04 レビュー是正(Medium・裁定20): `state:step`
            # (status=finished)で Tool 境界を閉じる。旧実装は最初の Tool
            # 開始で一度 `set()` したら二度と `clear()` されず、以降の
            # メイン LLM 呼び出し(次周の判断・`respond` のストリーミング)
            # まで「Tool 中」として切断キャンセルの対象外になっていた
            # (chat_sse.md §1.6: 切断時に打ち切るのはメイン LLM 呼び出しだけ、
            # Tool の実行は完了させる、という区別を表現できていなかった)。
            if adapted.root.data.kind == "step" and adapted.root.data.status == "finished":
                self.tool_phase_started.clear()
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


class ValidatingEventSink:
    """テスト用シンク: `domains.conversation.events.MemoryEventSink` と同じ

    記録に加え、各イベントを **API Pydantic 契約**(`adapt_conversation_event`)
    まで通す(2026-08-04、レビュー是正: テストの穴 §3)。

    `MemoryEventSink` は内部 `ConversationEvent` をそのまま保持するだけで
    API 契約の検証を経由しないため、`stage="act"` のような契約外の値を
    テストが検出できなかった(層 1 テストの穴)。本シンクは `emit` のたびに
    `ChatEventBuffer` と同じ変換を通すため、契約違反があれば
    `pydantic.ValidationError` としてテストが直接失敗する。
    """

    def __init__(self) -> None:
        self.events: list[ConversationEvent] = []
        self.validated: list[ChatEvent] = []

    async def emit(self, event: ConversationEvent) -> None:
        self.events.append(event.model_copy(deep=True))
        # `ChatEventBuffer.emit` と異なり、ここでは意図的に例外を握り潰さない
        # (テストに契約違反を伝播させるのが目的のため)。
        self.validated.append(adapt_conversation_event(event))

    @property
    def sequence(self) -> list[str]:
        return [
            f"{event.event}:{event.data.get('kind', event.data.get('code', ''))}".rstrip(":")
            for event in self.events
        ]


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
                "name_ja": (
                    name if isinstance(name, str) else spot_names.get(spot_id, UNNAMED_SPOT_JA)
                ),
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
