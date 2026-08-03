"""`ToolAdapters.ask_user`(§7 の HITL 実体)の層 1 仕様。

実 DB は使わない。`pending_ask_writer` を差し替えて、別トランザクション
書き込みの「呼ばれ方」だけを検査する(実際の別コミット可視性は
`tests/integration/test_ask_pending_database.py` が compose DB で検証する)。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domains.conversation.ask_registry import AskAnswer, AskUserRegistry
from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.tool_adapters import ToolAdapters
from app.domains.conversation.types import AskUserArgs


class RecordingPendingAskWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, *, thread_id: int, pending: dict[str, Any] | None, settings: Any
    ) -> None:
        del settings
        self.calls.append({"thread_id": thread_id, "pending": pending})


def _preference_args() -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "preference",
            "slot": "mobility",
            "reason": "どのくらい歩けますか",
            "options": [
                {"label": "あまり歩きたくない", "value": "avoid_walk"},
                {"label": "30分程度なら", "value": "short_walk_ok"},
            ],
        }
    )


def _adapter(*, writer: RecordingPendingAskWriter, timeout_sec: float = 5.0) -> ToolAdapters:
    registry = AskUserRegistry()
    return ToolAdapters(
        cast(AsyncSession, None),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        ask_timeout_sec=timeout_sec,
        pending_ask_writer=writer,
    )


async def test_ask_user_waits_for_the_registry_answer_and_returns_ask_result() -> None:
    writer = RecordingPendingAskWriter()
    adapter = _adapter(writer=writer)

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        resolved = adapter.ask_registry.resolve(
            42, AskAnswer(answer="30分程度なら", answered_by="chip")
        )
        assert resolved is True

    asyncio.create_task(answer_soon())
    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert result.data["answer"] == "30分程度なら"
    assert result.data["answered_by"] == "chip"
    # 1 回目 = 質問の書き込み、2 回目 = 回答受領後の即時クリア。
    assert [call["pending"] is not None for call in writer.calls] == [True, False]
    assert writer.calls[0]["pending"]["kind"] == "preference"
    assert writer.calls[0]["pending"]["slot"] == "mobility"
    assert "asked_at" in writer.calls[0]["pending"]
    assert writer.calls[1]["thread_id"] == 1


async def test_ask_user_timeout_returns_answered_by_timeout_and_still_clears_pending() -> None:
    writer = RecordingPendingAskWriter()
    adapter = _adapter(writer=writer, timeout_sec=0.02)

    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert result.data["answered_by"] == "timeout"
    assert result.data["answer"]  # 空文字ではない(AskUserResult.answer は min_length=1)
    assert [call["pending"] is not None for call in writer.calls] == [True, False]


async def test_ask_user_registers_waiter_before_emitting_sse_or_writing_pending() -> None:
    """裁定6(2026-08-04レビュー是正): 順序は

    ①レジストリに waiter 登録 → ②SSE送出 → ③pending_ask 書き込み。
    ②③どちらの時点でも `ask_registry.is_waiting` が既に True であること
    (`GET /thread` が「死んだ待機」と誤認して掃除しない条件)を確認する。
    """

    log: list[str] = []
    registry = AskUserRegistry()

    class RecordingSink:
        async def emit(self, event: Any) -> None:
            del event
            log.append(f"sse:is_waiting={registry.is_waiting(42)}")

    class RecordingWriter:
        async def __call__(
            self, *, thread_id: int, pending: Any, settings: Any
        ) -> None:
            del thread_id, settings
            if pending is not None:
                log.append(f"pending_write:is_waiting={registry.is_waiting(42)}")
            else:
                log.append("pending_clear")

    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=RecordingSink(),
        settings=get_settings(),
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        ask_timeout_sec=5.0,
        pending_ask_writer=RecordingWriter(),
    )

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        registry.resolve(42, AskAnswer(answer="30分程度なら", answered_by="chip"))

    asyncio.create_task(answer_soon())
    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert log == [
        "sse:is_waiting=True",
        "pending_write:is_waiting=True",
        "pending_clear",
    ]
    assert result.data["answer"] == "30分程度なら"


async def test_ask_user_answer_arriving_during_sse_emit_still_resolves() -> None:
    """表示直後の即答が 409(取りこぼし)にならない。

    旧実装は SSE 送出 → DB 書き込み → レジストリ登録の順だったため、
    フォーム表示直後にユーザーが即答すると waiter が未登録で回答を
    取りこぼしていた(表示直後の回答が失敗する実バグ)。
    """

    registry = AskUserRegistry()

    class ImmediateAnswerSink:
        async def emit(self, event: Any) -> None:
            del event
            resolved = registry.resolve(
                42, AskAnswer(answer="30分程度なら", answered_by="chip")
            )
            assert resolved is True

    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=ImmediateAnswerSink(),
        settings=get_settings(),
        thread_id=1,
        user_id=42,
        ask_registry=registry,
        ask_timeout_sec=5.0,
        pending_ask_writer=RecordingPendingAskWriter(),
    )

    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert result.data["answer"] == "30分程度なら"
    assert result.data["answered_by"] == "chip"


async def test_ask_user_skips_pending_write_when_thread_id_is_none() -> None:
    writer = RecordingPendingAskWriter()
    registry = AskUserRegistry()
    adapter = ToolAdapters(
        cast(AsyncSession, None),
        event_sink=MemoryEventSink(),
        settings=get_settings(),
        thread_id=None,
        user_id=None,
        ask_registry=registry,
        ask_timeout_sec=0.02,
        pending_ask_writer=writer,
    )

    result = await adapter.ask_user(step_id=1, args=_preference_args())

    assert writer.calls == []
    assert result.data["answered_by"] == "timeout"  # user_id が無いので待機できない
