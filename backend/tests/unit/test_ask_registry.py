"""`ask_user` の HITL レジストリ(§7)の層 1 仕様。

待機・回答・タイムアウトの 3 分岐を検査する。タイムアウトはテストを速く
保つため短い値を明示的に渡す(既定 10 分は本番用)。
"""

from __future__ import annotations

import asyncio

from app.domains.conversation.ask_registry import (
    AskAnswer,
    AskUserRegistry,
    wait_for_answer,
)


async def test_wait_for_answer_returns_the_resolved_answer() -> None:
    registry = AskUserRegistry()

    async def answer_soon() -> None:
        await asyncio.sleep(0)
        resolved = registry.resolve(
            1, AskAnswer(answer="鶴間池", answered_by="chip", resolves={"surface": "2番目"})
        )
        assert resolved is True

    waiter = asyncio.create_task(wait_for_answer(registry, key=1, timeout_sec=5))
    asyncio.create_task(answer_soon())
    result = await waiter

    assert result == AskAnswer(answer="鶴間池", answered_by="chip", resolves={"surface": "2番目"})
    assert registry.is_waiting(1) is False


async def test_wait_for_answer_times_out_and_returns_none() -> None:
    registry = AskUserRegistry()

    result = await wait_for_answer(registry, key=2, timeout_sec=0.05)

    assert result is None
    assert registry.is_waiting(2) is False


async def test_resolve_without_a_waiter_returns_false() -> None:
    registry = AskUserRegistry()

    resolved = registry.resolve(3, AskAnswer(answer="はい", answered_by="free_text"))

    assert resolved is False


async def test_resolve_after_timeout_no_longer_succeeds() -> None:
    registry = AskUserRegistry()

    await wait_for_answer(registry, key=4, timeout_sec=0.02)
    late_resolution = registry.resolve(4, AskAnswer(answer="遅い", answered_by="free_text"))

    assert late_resolution is False


async def test_is_waiting_reflects_live_state_only() -> None:
    registry = AskUserRegistry()
    assert registry.is_waiting(5) is False

    future = registry.begin(5)
    assert registry.is_waiting(5) is True

    future.set_result(AskAnswer(answer="ok", answered_by="free_text"))
    # done() な Future はもう「待機中」ではない。
    assert registry.is_waiting(5) is False

    registry.end(5)
    assert registry.is_waiting(5) is False


async def test_second_wait_for_the_same_key_replaces_the_first() -> None:
    """1 ターン内で直列にしか質問しない前提(ToolAdapters.ask_user)。

    2 回目の `begin` は 1 回目の Future を置き換える(同時待機は起こらない)。
    """

    registry = AskUserRegistry()
    first = registry.begin(6)
    second = registry.begin(6)

    assert first is not second
    resolved = registry.resolve(6, AskAnswer(answer="2つ目", answered_by="free_text"))
    assert resolved is True
    assert second.result() == AskAnswer(answer="2つ目", answered_by="free_text")
    assert first.done() is False
