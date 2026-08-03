"""`ask_user` の HITL 待ち受けレジストリ(§7)。

`Docs/30_design/agent_react_architecture.md` §7 の機構: ターンの処理
(コルーチン)は回答を待って停止するが、ターンをまたぐプロセス内状態は持たない
(待機はターン内で完結する。ADR-0004 と衝突しない)。

プロセス内に「ユーザー id → 回答待ち」のレジストリを 1 つ持てば足りる(1 ユーザー
1 スレッドなので、ユーザー id で一意にターンを特定できる)。API 層では
`api/routers/chat.py` の `ActiveTurnRegistry` と同じ流儀で `request.app.state`
に 1 個だけ置き、`ToolAdapters` へ明示的に注入する(隠れたモジュールグローバルに
しない。テストごとに独立させるため)。

`threads.pending_ask` への即時書き込み/即時クリアも本モジュールに置く。
ターンの主 session(commit は persist まで先延ばし)とは別の一時 session で
書くのは、質問が表示されている間も `GET /thread` が見えるようにするため。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import update

from app.core.config import Settings
from app.core.db import get_session_factory
from app.db_models import Thread

DEFAULT_ASK_TIMEOUT_SEC = 600.0  # 10分(§7)


@dataclass(frozen=True, slots=True)
class AskAnswer:
    """`POST /api/v1/chat/answer` から届く回答。"""

    answer: str
    answered_by: Literal["chip", "free_text"]
    resolves: dict[str, Any] | None = None


class AskUserRegistry:
    """ユーザー id → 回答待ち `Future` の 1 プロセス内レジストリ。"""

    def __init__(self) -> None:
        self._waiters: dict[int, asyncio.Future[AskAnswer]] = {}

    def is_waiting(self, key: int) -> bool:
        future = self._waiters.get(key)
        return future is not None and not future.done()

    def begin(self, key: int) -> asyncio.Future[AskAnswer]:
        """新しい待機を登録する(既存の待機があれば上書きする。

        `ToolAdapters.ask_user` は 1 ターン内で直列にしか質問しないため、
        同一 key への同時待機は起こらない前提)。
        """

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AskAnswer] = loop.create_future()
        self._waiters[key] = future
        return future

    def resolve(self, key: int, answer: AskAnswer) -> bool:
        """回答を待っている Future があれば解決する。無ければ False(409 用)。"""

        future = self._waiters.get(key)
        if future is None or future.done():
            return False
        future.set_result(answer)
        return True

    def end(self, key: int) -> None:
        """待機を終える(タイムアウト・回答受領のどちらでも呼ぶ)。"""

        self._waiters.pop(key, None)


async def wait_for_answer(
    registry: AskUserRegistry,
    *,
    key: int,
    timeout_sec: float = DEFAULT_ASK_TIMEOUT_SEC,
) -> AskAnswer | None:
    """回答を待つ。タイムアウトで `None` を返す(呼び出し元が timeout 扱いにする)。"""

    future = registry.begin(key)
    try:
        return await asyncio.wait_for(future, timeout=timeout_sec)
    except (TimeoutError, asyncio.CancelledError):
        return None
    finally:
        registry.end(key)


async def write_pending_ask_now(
    *,
    thread_id: int,
    pending: dict[str, Any] | None,
    settings: Settings,
) -> None:
    """`threads.pending_ask` をターンの主トランザクションとは別に即時反映する。

    ターンの主 session は persist まで commit しない(§13 の 1 トランザクション
    境界)。質問が表示されている間・回答直後だけ、この専用の一時 session で
    別コミットする(進行中でも `GET /thread` が見えるように。§7)。
    """

    factory = get_session_factory(settings)
    async with factory() as session:
        await session.execute(
            update(Thread).where(Thread.id == thread_id).values(pending_ask=pending)
        )
        await session.commit()


__all__ = [
    "DEFAULT_ASK_TIMEOUT_SEC",
    "AskAnswer",
    "AskUserRegistry",
    "wait_for_answer",
    "write_pending_ask_now",
]
