"""SSE アダプタ、フレーミング、ハートビートの単体契約。"""

import asyncio
import json
from typing import Any

import pytest

from app.api.schemas.chat import ChatEvent
from app.api.sse import (
    HEARTBEAT_FRAME,
    ChatEventBuffer,
    DisconnectAwareGenerationClient,
    ValidatingEventSink,
    adapt_conversation_event,
    frame_sse,
    iter_sse_frames,
)
from app.domains.conversation.ask_registry import AskUserRegistry
from app.domains.conversation.events import (
    ConversationEvent,
    MemoryEventSink,
    state_event,
)
from app.domains.conversation.tool_adapters import ToolAdapters
from app.domains.conversation.types import AskUserArgs


def test_candidate_internal_names_are_mapped_to_public_contract() -> None:
    event = ConversationEvent(
        event="state",
        data={
            "kind": "candidates",
            "stage": "provisional",
            "spot_ids": ["spot_012"],
            "candidates": [
                {
                    "spot_id": "spot_012",
                    "rank": 1,
                    "reason_materials": {"matched_tags": ["滝"]},
                }
            ],
        },
    )

    adapted = adapt_conversation_event(
        event,
        spot_names={"spot_012": "鶴間池"},
    )
    payload = adapted.root.data.model_dump(mode="json")

    assert payload == {
        "kind": "candidates",
        "phase": "provisional",
        "items": [
            {
                "spot_id": "spot_012",
                "name_ja": "鶴間池",
                "reason_materials": {"matched_tags": ["滝"]},
            }
        ],
    }
    assert "stage" not in payload
    assert "spot_ids" not in payload
    assert "candidates" not in payload


async def test_ask_user_and_clarify_sse_include_reason_without_changing_options() -> None:
    sink = MemoryEventSink()
    adapter = object.__new__(ToolAdapters)
    adapter.event_sink = sink
    # thread_id/user_id を None にして、pending_ask の DB 書き込みと
    # レジストリでの待機をスキップする(この層はイベント本文だけを検査する)。
    adapter.thread_id = None
    adapter.user_id = None
    adapter.ask_registry = AskUserRegistry()

    await adapter.ask_user(
        step_id=1,
        args=AskUserArgs.model_validate(
            {
                "kind": "preference",
                "slot": "origin",
                "reason": "仮定した旅程条件の確認",
                "options": [
                    {"label": "この条件で進める", "value": "accept_assumptions"},
                    {"label": "条件を変更する", "value": "change_conditions"},
                ],
            }
        ),
    )
    await adapter.ask_user(
        step_id=2,
        args=AskUserArgs.model_validate(
            {
                "kind": "clarify",
                "surface": "2番目",
                "reason": "候補が複数あります",
                "options": [
                    {"label": "鶴間池", "value": "spot_001"},
                    {"label": "元滝伏流水", "value": "spot_002"},
                ],
            }
        ),
    )

    preference, clarify = [
        adapt_conversation_event(event).root.data.model_dump(mode="json")
        for event in sink.events
    ]

    assert preference == {
        "kind": "ask_user",
        "slot": "origin",
        "reason": "仮定した旅程条件の確認",
        "options": ["この条件で進める", "条件を変更する"],
    }
    assert clarify == {
        "kind": "clarify",
        "surface": "2番目",
        "reason": "候補が複数あります",
        "options": [
            {"label": "鶴間池", "value": "spot_001"},
            {"label": "元滝伏流水", "value": "spot_002"},
        ],
    }


async def test_search_knowledge_progress_is_translated_to_state_step() -> None:
    """旧 `searching` は `state:step`(status=progress)へ統合する(ADR-0019)。

    narration ドメイン内部(`SearchStateEvent`)は変えず、変換は conversation
    側(`ToolAdapters.search_knowledge`)で行う。
    """

    from app.core.config import get_settings
    from app.domains.narration.search.types import SearchResult, SearchStateEvent

    sink = MemoryEventSink()
    adapter = object.__new__(ToolAdapters)
    adapter.event_sink = sink
    adapter.settings = get_settings()

    async def fake_search_runner(request: str, spot_id: str | None, **kwargs: Any) -> Any:
        del request, spot_id
        event_sink = kwargs["event_sink"]
        await event_sink(SearchStateEvent(text="鶴間池の資料を読んでいます"))
        return SearchResult(answer_ja="回答", sources=[], coverage="none")

    adapter.search_runner = fake_search_runner

    from app.domains.conversation.types import SearchKnowledgeArgs

    await adapter.search_knowledge(
        step_id=1,
        args=SearchKnowledgeArgs(request="由来を教えて"),
    )

    assert len(sink.events) == 1
    payload = adapt_conversation_event(sink.events[0]).root.data.model_dump(mode="json")
    assert payload == {
        "kind": "step",
        "tool": "search_knowledge",
        "status": "progress",
        "label_ja": "鶴間池の資料を読んでいます",
    }


def test_sse_frame_has_event_data_and_terminal_blank_line() -> None:
    event = ChatEvent.model_validate(
        {"event": "token", "data": {"text": "鳥海山です"}}
    )

    framed = frame_sse(event)

    assert framed.startswith(b"event: token\ndata: ")
    assert framed.endswith(b"\n\n")
    payload = framed.decode().split("data: ", 1)[1].strip()
    assert json.loads(payload) == {"text": "鳥海山です"}


async def test_heartbeat_is_inserted_when_no_token_arrives() -> None:
    queue: asyncio.Queue[ChatEvent | object] = asyncio.Queue()
    frames = iter_sse_frames(queue, heartbeat_sec=0.01)

    first = await asyncio.wait_for(anext(frames), timeout=0.2)
    await frames.aclose()

    assert first == HEARTBEAT_FRAME


async def test_disconnect_cancels_understand_but_not_tool_generation() -> None:
    class Client:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def generate(self, messages: Any, **kwargs: Any) -> str:
            del messages, kwargs
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return "completed"

    disconnected = asyncio.Event()
    tool_phase = asyncio.Event()
    client = Client()
    proxy = DisconnectAwareGenerationClient(
        client,
        disconnected,
        tool_phase_started=tool_phase,
    )
    understand = asyncio.create_task(proxy.generate([]))
    await client.started.wait()
    disconnected.set()

    with pytest.raises(asyncio.CancelledError):
        await understand
    assert client.cancelled.is_set()

    tool_client = Client()
    tool_phase.set()
    tool_proxy = DisconnectAwareGenerationClient(
        tool_client,
        disconnected,
        tool_phase_started=tool_phase,
    )
    tool = asyncio.create_task(tool_proxy.generate([]))
    await tool_client.started.wait()
    tool_client.release.set()

    assert await tool == "completed"


# ---------------------------------------------------------------------------
# 2026-08-04 レビュー是正: Critical(stage="act" の安全網)・裁定20(tool_phase_started の境界)
# ---------------------------------------------------------------------------


async def test_chat_event_buffer_falls_back_safely_on_invalid_error_stage() -> None:
    """Critical 是正: `error_event` が既に検証しているので通常は起きないが、

    `ChatEventBuffer.emit` 自体も契約違反を安全側(`stage=main_agent`)へ
    落として例外を投げない(Tool の結果適用の途中で例外が伝播し、persist
    への到達を妨げないようにする防御)。
    """

    buffer = ChatEventBuffer(fallback_turn_id="fallback")
    # 直接 ConversationEvent を組み立てて、`error_event` の検証を迂回する
    # (「万一契約外のイベントが来ても buffer は落ちない」ことを確認するため)。
    bad_event = ConversationEvent(
        event="error",
        data={"stage": "act", "code": "route_degraded", "degraded": True, "message": "縮退"},
    )

    await buffer.emit(bad_event)  # 例外を投げない

    queued = await buffer.queue.get()
    assert queued.root.event == "error"
    assert queued.root.data.stage == "main_agent"
    assert queued.root.data.code == "internal"


async def test_chat_event_buffer_drops_invalid_state_event_without_raising() -> None:
    """error 以外の契約違反イベントは、代替できないため落として続行する。"""

    buffer = ChatEventBuffer(fallback_turn_id="fallback")
    bad_event = ConversationEvent(event="state", data={"kind": "unknown_kind"})

    await buffer.emit(bad_event)  # 例外を投げない

    assert buffer.queue.empty()


async def test_validating_event_sink_raises_on_invalid_stage() -> None:
    """テストの穴 §3 是正: `MemoryEventSink` は API 契約を検証しないため

    `stage="act"` のような契約外の値を検出できなかった。`ValidatingEventSink`
    は同じ変換を通すため、契約違反が `ValidationError` として直接失敗する。
    `error_event` 自身は既に安全側へ倒すため、契約違反を直接テストするには
    検証をバイパスして `ConversationEvent` を組み立てる。
    """

    from pydantic import ValidationError

    sink = ValidatingEventSink()
    raw_event = ConversationEvent(
        event="error",
        data={"stage": "act", "code": "route_degraded", "degraded": True, "message": "縮退"},
    )

    with pytest.raises(ValidationError):
        await sink.emit(raw_event)

    # 検証に失敗する前でも記録(`.events`)は行う。
    assert len(sink.events) == 1


async def test_tool_phase_started_is_cleared_when_the_step_finishes() -> None:
    """裁定20(2026-08-04レビュー是正): `state:step`(finished)で Tool 境界を

    閉じる。以前は最初の Tool 開始で一度立てたら二度と下ろされず、以降の
    メイン LLM 呼び出し(次周の判断・respond のストリーミング)まで
    「Tool 中」として切断キャンセルの対象外になっていた。
    """

    buffer = ChatEventBuffer(fallback_turn_id="fallback")
    assert buffer.tool_phase_started.is_set() is False

    await buffer.emit(state_event("step", tool="recommend", status="started", label_ja="探索中"))
    assert buffer.tool_phase_started.is_set() is True

    await buffer.emit(
        state_event(
            "candidates",
            phase="provisional",
            items=[{"spot_id": "spot_001", "name_ja": "鶴間池"}],
        )
    )
    assert buffer.tool_phase_started.is_set() is True  # Tool の途中では立ったまま

    await buffer.emit(state_event("step", tool="recommend", status="finished", label_ja="完了"))
    assert buffer.tool_phase_started.is_set() is False  # Tool 境界で下りる

    # 次の Tool が始まれば、また立つ。
    await buffer.emit(
        state_event("step", tool="plan_itinerary", status="started", label_ja="作成中")
    )
    assert buffer.tool_phase_started.is_set() is True
