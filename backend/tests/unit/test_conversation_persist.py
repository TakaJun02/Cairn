"""N6 `persist` の層1仕様(§8。2026-08-04レビュー是正・裁定19)。

`persist()` は `done` を emit したら履歴要約の完了を待たずに返ること、
履歴要約は独立した repository/セッション(`history_repository_factory`)で
バックグラウンド実行されることを検査する。以前は要約 LLM 呼び出しを
`await` してから返っており、`done` がクライアントへ届くまで・
`ActiveTurnRegistry` が解放されるまでの両方をブロックしていた。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.history_summary import HistorySummaryState
from app.domains.conversation.persist import persist
from app.domains.conversation.state import MessageState, ProfileState, TurnState


def _state() -> TurnState:
    return TurnState(
        turn_id="turn", thread_id=1, user_id=1, utterance="x", profile=ProfileState()
    )


def _messages() -> list[MessageState]:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    roles = ["user", "assistant"] * 3
    return [
        MessageState(
            id=index + 1,
            seq=index + 1,
            role=role,
            content=f"m{index + 1}",
            status="complete",
            meta={},
            created_at=now,
        )
        for index, role in enumerate(roles)
    ]


class FakeRepository:
    """`persist_turn` だけを満たす(履歴要約メソッドは持たない)。"""

    def __init__(self, *, message_id: int | None = 88) -> None:
        self.message_id = message_id
        self.persist_calls = 0

    async def persist_turn(self, state: TurnState) -> int | None:
        self.persist_calls += 1
        return self.message_id


class SlowHistoryRepository:
    """要約完了をテストから待てるよう `asyncio.Event` を公開する。"""

    def __init__(self, *, delay: float) -> None:
        self.delay = delay
        self.committed: dict[str, Any] | None = None
        self.done = asyncio.Event()
        self.load_calls = 0

    async def load_history_summary_state(self, thread_id: int) -> HistorySummaryState:
        self.load_calls += 1
        await asyncio.sleep(self.delay)
        return HistorySummaryState(
            history_summary="", summarized_until_message_id=None, messages=_messages()
        )

    async def commit_history_summary(
        self, thread_id: int, *, summary: str, summarized_until_message_id: int
    ) -> None:
        self.committed = {
            "summary": summary,
            "summarized_until_message_id": summarized_until_message_id,
        }
        self.done.set()


class ScriptedClient:
    def __init__(self, response: str) -> None:
        self.response = response

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        return self.response


async def test_persist_returns_before_history_summary_completes() -> None:
    """裁定19: `persist()` は要約の完了を待たずに返る(done はクリティカル

    パスから独立)。"""

    sink = MemoryEventSink()
    repository = FakeRepository()
    history_repo = SlowHistoryRepository(delay=0.05)

    @asynccontextmanager
    async def factory():
        yield history_repo

    started = asyncio.get_running_loop().time()
    message_id = await persist(
        _state(),
        repository=repository,
        event_sink=sink,
        history_client=ScriptedClient("要約結果"),
        history_repository_factory=factory,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert message_id == 88
    assert elapsed < 0.05  # 要約(delay=0.05秒)を待たずに返っている
    assert sink.events[-1].event == "done"
    assert history_repo.committed is None  # まだ完了していない

    await asyncio.wait_for(history_repo.done.wait(), timeout=2)
    assert history_repo.committed is not None
    assert history_repo.committed["summary"] == "要約結果"


async def test_persist_uses_the_factory_repository_not_the_main_one() -> None:
    """要約は独立した repository/セッションで動く(主セッションの再利用禁止。

    `ask_registry.write_pending_ask_now` と同じ理由)。"""

    repository = FakeRepository()
    history_repo = SlowHistoryRepository(delay=0.0)

    @asynccontextmanager
    async def factory():
        yield history_repo

    await persist(
        _state(),
        repository=repository,
        history_client=ScriptedClient("要約"),
        history_repository_factory=factory,
    )
    await asyncio.wait_for(history_repo.done.wait(), timeout=2)

    assert history_repo.load_calls == 1
    assert repository.persist_calls == 1
    # `repository`(FakeRepository)には要約メソッドが無いことを確認する
    # (= 要約は確実に factory の repository で行われた)。
    assert not hasattr(repository, "load_history_summary_state")


async def test_persist_without_factory_falls_back_to_reusing_the_repository() -> None:
    """factory を渡さない(テストの Fake 向け後方互換)場合は repository を

    そのまま使う。"""

    class CombinedRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.history_repo = SlowHistoryRepository(delay=0.0)

        async def load_history_summary_state(self, thread_id: int) -> HistorySummaryState:
            return await self.history_repo.load_history_summary_state(thread_id)

        async def commit_history_summary(
            self, thread_id: int, *, summary: str, summarized_until_message_id: int
        ) -> None:
            await self.history_repo.commit_history_summary(
                thread_id,
                summary=summary,
                summarized_until_message_id=summarized_until_message_id,
            )

    repository = CombinedRepository()

    await persist(_state(), repository=repository, history_client=ScriptedClient("要約"))
    await asyncio.wait_for(repository.history_repo.done.wait(), timeout=2)

    assert repository.history_repo.committed is not None


async def test_persist_skips_history_summary_when_repository_lacks_the_methods() -> None:
    repository = FakeRepository()

    message_id = await persist(_state(), repository=repository)

    assert message_id == 88  # 例外にならず、要約は静かにスキップされる


async def test_history_summary_background_failure_does_not_raise() -> None:
    """factory の構築失敗や要約失敗は例外として伝播しない(NFR-5)。"""

    repository = FakeRepository()

    @asynccontextmanager
    async def failing_factory():
        raise RuntimeError("セッション取得に失敗しました")
        yield  # pragma: no cover - 到達しない

    message_id = await persist(
        _state(),
        repository=repository,
        history_repository_factory=failing_factory,
    )

    assert message_id == 88
    # バックグラウンドタスクが例外で落ちても待たされない/伝播しないことの確認。
    await asyncio.sleep(0.01)
