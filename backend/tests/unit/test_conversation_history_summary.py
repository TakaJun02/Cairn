"""⑤ persist 内・done 送出後に走る履歴要約の畳み込み更新。

押し出し判定（生層より前・未畳み込みのターンだけ畳む）と、失敗時に
`summarized_until_message_id` が進まないこと（NFR-5 の縮退）を検査する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domains.conversation.history_summary import (
    HistorySummaryState,
    update_history_summary,
)
from app.domains.conversation.state import MessageState, ProfileState, TurnState


def _message(id_: int, role: str, content: str) -> MessageState:
    return MessageState(
        id=id_,
        seq=id_,
        role=role,
        content=content,
        status="complete",
        meta={},
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def _state() -> TurnState:
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="test",
        profile=ProfileState(),
    )


class FakeHistorySummaryRepository:
    def __init__(self, state: HistorySummaryState) -> None:
        self.state = state
        self.committed: dict[str, Any] | None = None

    async def load_history_summary_state(self, thread_id: int) -> HistorySummaryState:
        assert thread_id == 1
        return self.state

    async def commit_history_summary(
        self,
        thread_id: int,
        *,
        summary: str,
        summarized_until_message_id: int,
    ) -> None:
        assert thread_id == 1
        self.committed = {
            "summary": summary,
            "summarized_until_message_id": summarized_until_message_id,
        }


class ScriptedClient:
    def __init__(self, response: str | BaseException) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del kwargs
        self.calls.append(messages)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


async def test_folds_turns_pushed_out_of_the_raw_window_and_advances_boundary() -> None:
    messages = [
        _message(1, "user", "鶴間池について知りたい"),
        _message(2, "assistant", "紹介しました"),
        _message(3, "user", "他にもある？"),
        _message(4, "assistant", "元滝伏流水も紹介しました"),
        _message(5, "user", "1日目に入れて"),
        _message(6, "assistant", "旅程へ入れました"),
        _message(7, "user", "昼休憩も"),
        _message(8, "assistant", "昼休憩を入れました"),
    ]
    repository = FakeHistorySummaryRepository(
        HistorySummaryState(
            history_summary="",
            summarized_until_message_id=None,
            messages=messages,
        )
    )
    client = ScriptedClient("鶴間池と元滝伏流水を紹介した。")

    await update_history_summary(_state(), repository=repository, client=client)

    assert repository.committed == {
        "summary": "鶴間池と元滝伏流水を紹介した。",
        # 直近2ターン（5-6, 7-8）より前の最後のターン（3-4）まで畳み込む。
        "summarized_until_message_id": 4,
    }
    # 既存要約とこれから畳み込むターンの両方が LLM 入力に含まれる。
    prompt = client.calls[0][1]["content"]
    assert "鶴間池について知りたい" in prompt
    assert "元滝伏流水も紹介しました" in prompt
    assert "1日目に入れて" not in prompt  # 直近2ターンは畳み込み対象外


async def test_nothing_to_fold_skips_the_llm_call() -> None:
    messages = [
        _message(1, "user", "こんにちは"),
        _message(2, "assistant", "こんにちは"),
    ]
    repository = FakeHistorySummaryRepository(
        HistorySummaryState(
            history_summary="",
            summarized_until_message_id=None,
            messages=messages,
        )
    )
    client = ScriptedClient("使われないはず")

    await update_history_summary(_state(), repository=repository, client=client)

    assert client.calls == []
    assert repository.committed is None


async def test_already_folded_turns_are_not_folded_again() -> None:
    messages = [
        _message(1, "user", "u1"),
        _message(2, "assistant", "a1"),
        _message(3, "user", "u2"),
        _message(4, "assistant", "a2"),
        _message(5, "user", "u3"),
        _message(6, "assistant", "a3"),
    ]
    repository = FakeHistorySummaryRepository(
        HistorySummaryState(
            history_summary="既存の要約。",
            summarized_until_message_id=6,  # 全ターンが直近2ターンの中か、既に畳み込み済み
            messages=messages,
        )
    )
    client = ScriptedClient("使われないはず")

    await update_history_summary(_state(), repository=repository, client=client)

    assert client.calls == []
    assert repository.committed is None


async def test_llm_failure_is_logged_and_summarized_until_message_id_does_not_advance() -> None:
    messages = [
        _message(1, "user", "u1"),
        _message(2, "assistant", "a1"),
        _message(3, "user", "u2"),
        _message(4, "assistant", "a2"),
        _message(5, "user", "u3"),
        _message(6, "assistant", "a3"),
        _message(7, "user", "u4"),
        _message(8, "assistant", "a4"),
    ]
    repository = FakeHistorySummaryRepository(
        HistorySummaryState(
            history_summary="既存の要約。",
            summarized_until_message_id=None,
            messages=messages,
        )
    )
    client = ScriptedClient(RuntimeError("生成サーバーが応答しません"))

    # 例外を上げずに完了する（NFR-5）。
    await update_history_summary(_state(), repository=repository, client=client)

    assert repository.committed is None


async def test_repository_failure_is_swallowed_too() -> None:
    class BrokenRepository:
        async def load_history_summary_state(self, thread_id: int) -> HistorySummaryState:
            raise RuntimeError("DB down")

    # 例外を上げずに完了する。
    await update_history_summary(
        _state(), repository=BrokenRepository(), client=ScriptedClient("x")
    )
