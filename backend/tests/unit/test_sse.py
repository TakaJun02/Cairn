"""SSE アダプタ、フレーミング、ハートビートの単体契約。"""

import asyncio
import json
from typing import Any

import pytest

from app.api.schemas.chat import ChatEvent
from app.api.sse import (
    HEARTBEAT_FRAME,
    DisconnectAwareGenerationClient,
    adapt_conversation_event,
    frame_sse,
    iter_sse_frames,
)
from app.domains.conversation.events import ConversationEvent, MemoryEventSink
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
