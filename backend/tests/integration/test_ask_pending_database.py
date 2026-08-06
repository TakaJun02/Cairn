"""`write_pending_ask_now` が主トランザクションとは別に即時反映されることを

compose DB で検証する(§7: 質問が表示されている間・回答直後も
`GET /thread` が見えるように、専用の一時 session で別コミットする)。

`test_plan_itinerary_then_ask_user_does_not_self_deadlock` は Critical 是正
(2026-08-04)の回帰テスト: `plan_itinerary → ask_user` の順で 1 ターン内に
実行されると、`ItineraryRepository.get_pending_constraints` が `threads` 行を
`SELECT ... FOR UPDATE` し、`ask_user` の `write_pending_ask_now`(別セッション)
が同じ行への UPDATE をブロックされたまま待ち、その完了を待っているのは
ターン自身のコルーチンなので自己デッドロックになっていた。修正後は
`get_pending_constraints` が行ロックを取らないため、後続の別セッション書き込みが
即座に完了する。
"""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.db_models import Thread, User
from app.domains.conversation.ask_registry import write_pending_ask_now
from app.domains.itinerary.repo import ItineraryRepository

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


async def test_write_pending_ask_now_commits_independently_of_the_caller_transaction() -> None:
    user_name = f"ask-pending-{uuid4()}"
    settings = get_settings()
    try:
        async with session_scope() as setup_session:
            user = User(user_name=user_name, api_token=f"test-{uuid4()}")
            setup_session.add(user)
            await setup_session.flush()
            thread = Thread(user_id=user.id)
            setup_session.add(thread)
            await setup_session.flush()
            thread_id = thread.id

        # 主トランザクション役の session を開いたまま(commit しない)にする。
        # `write_pending_ask_now` は別 session/別コミットなので、これに
        # 影響されず即時反映される。
        async with session_scope() as main_session:
            main_thread = await main_session.scalar(
                select(Thread).where(Thread.id == thread_id)
            )
            assert main_thread is not None
            # 未コミットの変更をこの session に持たせる(pending_ask 以外の列)。
            main_thread.asked_slots = ["pace"]

            pending = {
                "kind": "clarify",
                "surface": "2番目",
                "reason": "候補が複数あります",
                "options": [{"label": "鶴間池", "value": "spot_001"}],
                "asked_at": "2026-08-04T10:00:00+09:00",
            }
            await write_pending_ask_now(
                thread_id=thread_id, pending=pending, settings=settings
            )

            # 別 session から読むと、主トランザクションの commit を待たずに
            # pending_ask だけが見える。
            async with session_scope() as reader_session:
                observed = await reader_session.scalar(
                    select(Thread.pending_ask).where(Thread.id == thread_id)
                )
                assert observed == pending

            await write_pending_ask_now(thread_id=thread_id, pending=None, settings=settings)

            async with session_scope() as reader_session:
                cleared = await reader_session.scalar(
                    select(Thread.pending_ask).where(Thread.id == thread_id)
                )
                assert cleared is None

            # 主トランザクション自体はここで初めて commit される
            # (session_scope の with を抜けるとき)。
    finally:
        async with session_scope() as session:
            existing_id = await session.scalar(
                select(User.id).where(User.user_name == user_name)
            )
            if existing_id is not None:
                await session.execute(delete(User).where(User.id == existing_id))
        await dispose_engine()


async def test_plan_itinerary_then_ask_user_does_not_self_deadlock() -> None:
    """Critical 是正の回帰テスト: 実経路 (`get_pending_constraints` → 別
    セッションの `write_pending_ask_now`) を通し、行ロックで自己デッドロック
    しないことを確認する。
    """

    user_name = f"ask-pending-deadlock-{uuid4()}"
    settings = get_settings()
    try:
        async with session_scope() as setup_session:
            user = User(user_name=user_name, api_token=f"test-{uuid4()}")
            setup_session.add(user)
            await setup_session.flush()
            thread = Thread(user_id=user.id)
            setup_session.add(thread)
            await setup_session.flush()
            user_id = user.id
            thread_id = thread.id

        # ターンの主セッション役: plan_itinerary が実際に呼ぶのと同じ
        # `ItineraryRepository.get_pending_constraints` を、commit しないまま
        # 呼び出す(persist はターンの最後まで先延ばしになるため)。
        async with session_scope() as main_session:
            repository = ItineraryRepository(main_session)
            pending = await repository.get_pending_constraints(user_id)
            assert pending == []

            # 同じターン内で ask_user が実行され、別セッション/別コミットで
            # pending_ask を書く(§7)。行ロックが残っていればここで
            # ブロックし続けるはずなので、短いタイムアウトで検知する。
            await asyncio.wait_for(
                write_pending_ask_now(
                    thread_id=thread_id,
                    pending={
                        "kind": "clarify",
                        "surface": "2番目",
                        "reason": "候補が複数あります",
                        "options": [{"label": "鶴間池", "value": "spot_001"}],
                        "asked_at": "2026-08-04T10:00:00+09:00",
                    },
                    settings=settings,
                ),
                timeout=5.0,
            )

            async with session_scope() as reader_session:
                observed = await reader_session.scalar(
                    select(Thread.pending_ask).where(Thread.id == thread_id)
                )
                assert observed is not None
                assert observed["surface"] == "2番目"

            await asyncio.wait_for(
                write_pending_ask_now(thread_id=thread_id, pending=None, settings=settings),
                timeout=5.0,
            )
    finally:
        async with session_scope() as session:
            existing_id = await session.scalar(
                select(User.id).where(User.user_name == user_name)
            )
            if existing_id is not None:
                await session.execute(delete(User).where(User.id == existing_id))
        await dispose_engine()
