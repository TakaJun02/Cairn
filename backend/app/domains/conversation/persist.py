"""N6 `persist`: respond の成否に関係なく通る 1 トランザクション境界。

`done` 送出後に履歴要約の畳み込み更新（history_summary.py）を走らせる
（§8）。応答のクリティカルパスには載らない位置であり、失敗しても
このターンの成否には影響しない。

2026-08-04 レビュー是正(Medium・裁定19): 以前は要約 LLM 呼び出しを
`persist()` 自身が `await` してから返っており、`ChatEventBuffer` は `done`
イベントを保留してターン完走(`finish()`)まで実際の SSE キューへ流さない
実装だったため、要約が終わるまでクライアントに `done` が届かず、
`ActiveTurnRegistry` の解放(次のターンの受理)もブロックしていた。

`persist()` は `done` を emit したら**すぐに返る**。履歴要約はバックグラウンド
タスクとして起動し、`await` しない(NFR-5: 失敗しても対話を止めない、を
文字どおり「ターンの完走を待たせない」まで徹底する)。バックグラウンドタスクは
ターンの主セッションとは独立したセッションで動く必要がある
(`ask_registry.write_pending_ask_now` と同じ理由: 主セッションは
`persist()` の呼び出し元 `async with session_scope(...)` が閉じうる)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from app.domains.conversation.events import EventSinkLike, done_event, emit
from app.domains.conversation.history_summary import (
    HISTORY_SUMMARY_WALLCLOCK_SEC,
    HistorySummaryRepositoryPort,
    update_history_summary,
)
from app.domains.conversation.state import TurnState

logger = logging.getLogger("app.conversation.persist")

HistoryRepositoryFactory = Callable[[], AbstractAsyncContextManager[HistorySummaryRepositoryPort]]

# バックグラウンドで起動した履歴要約タスクへの強参照を保持する集合。
# asyncio は「タスクへの外部参照が無いと GC されうる」ため
# (CPython 公式ドキュメントが明示的に警告する既知の落とし穴)、
# `asyncio.create_task()` の戻り値をどこかに保持し続ける必要がある。
_background_tasks: set[asyncio.Task[None]] = set()


class PersistRepositoryPort(Protocol):
    async def persist_turn(self, state: TurnState) -> int | None: ...


async def persist(
    state: TurnState,
    *,
    repository: PersistRepositoryPort,
    event_sink: EventSinkLike = None,
    history_client: Any | None = None,
    history_repository_factory: HistoryRepositoryFactory | None = None,
) -> int | None:
    """`done` を emit したら即座に返る(履歴要約はバックグラウンド)。

    `history_repository_factory` を渡すと、履歴要約はそれが返す**独立した**
    repository/セッションで動く(本番の配線。`pipeline.py` が
    `get_session_factory` から毎回新しいセッションを作る factory を渡す)。
    渡さなければ `repository` をそのまま使う(テストの Fake 向けの後方互換
    経路。Fake は実セッションのライフサイクルを持たないため安全)。
    """

    message_id = await repository.persist_turn(state)
    await emit(
        event_sink,
        done_event(
            turn_id=state.turn_id,
            message_id=message_id,
            degraded=bool(state.degraded),
        ),
    )
    _schedule_history_summary(
        state,
        repository=repository,
        history_client=history_client,
        factory=history_repository_factory,
    )
    return message_id


def _schedule_history_summary(
    state: TurnState,
    *,
    repository: PersistRepositoryPort,
    history_client: Any | None,
    factory: HistoryRepositoryFactory | None,
) -> None:
    resolved_factory = factory
    if resolved_factory is None:
        # factory が無ければ `repository` 自身が要約メソッドを持つときだけ
        # それを使い回す(テストの Fake 向けの後方互換経路)。factory が
        # 明示的に渡されていれば、`repository`(persist_turn 専用でもよい)
        # の形には関知せず常に進める(本番の配線がこの形)。
        if not (
            hasattr(repository, "load_history_summary_state")
            and hasattr(repository, "commit_history_summary")
        ):
            return
        resolved_factory = _reuse_repository_factory(repository)
    task = asyncio.create_task(
        _run_history_summary_in_background(
            state, factory=resolved_factory, history_client=history_client
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _reuse_repository_factory(repository: PersistRepositoryPort) -> HistoryRepositoryFactory:
    @asynccontextmanager
    async def factory() -> AsyncIterator[HistorySummaryRepositoryPort]:
        yield repository  # type: ignore[misc]

    return factory


async def _run_history_summary_in_background(
    state: TurnState,
    *,
    factory: HistoryRepositoryFactory,
    history_client: Any | None,
) -> None:
    try:
        async with asyncio.timeout(HISTORY_SUMMARY_WALLCLOCK_SEC):
            async with factory() as repository:
                await update_history_summary(state, repository=repository, client=history_client)
    except Exception:  # noqa: BLE001 - 要約失敗で対話を止めない(NFR-5)。
        # `update_history_summary` 自身も内部で例外を握り潰すが、
        # `factory()` の構築失敗(セッション取得等)はここでしか捕まえられない。
        logger.exception(
            "history_summary_background_failed",
            extra={"thread_id": state.thread_id, "turn_id": state.turn_id},
        )


__all__ = [
    "HistoryRepositoryFactory",
    "PersistRepositoryPort",
    "persist",
]
