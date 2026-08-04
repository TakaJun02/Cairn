"""`error_event` の契約検証(段2)。

`Docs/40_api/chat_sse.md` §1.2 の `ErrorStage` 語彙・
`Docs/30_design/agent_react_architecture.md` §13(不変条件)を検査する。

欠陥3(25 §1-6): 契約外 stage を渡したときの防御ログ(`invalid_error_stage`)
が `extra={"message": ...}` で LogRecord 予約キーを上書きし、防御そのものが
`KeyError` を投げてターン全体を落としていた。ここではその回帰を防ぐ
(実際のロガーで発火させ、例外が伝播しないことを検査する)。
"""

from __future__ import annotations

import logging

from app.domains.conversation.events import error_event, resolve_error_stage


def test_error_event_falls_back_to_main_agent_for_unknown_stage(caplog) -> None:
    """契約外 stage(例: "ask_user")は例外を投げずに "main_agent" へ落ちる。"""

    caplog.set_level(logging.WARNING, logger="app.conversation.events")

    event = error_event(
        stage="ask_user",
        code="internal",
        degraded=False,
        message="処理中に予期しない問題が発生しました。",
    )

    assert event.data["stage"] == "main_agent"
    assert event.data["code"] == "internal"
    assert event.data["message"] == "処理中に予期しない問題が発生しました。"
    # 防御ログ自体は発火する(観測可能性は保つ)が、例外は投げない
    # (旧実装は extra={"message": ...} で LogRecord 予約キーを上書きし
    # KeyError を投げていた)。
    records = [record for record in caplog.records if record.msg == "invalid_error_stage"]
    assert len(records) == 1
    assert records[0].error_message == "処理中に予期しない問題が発生しました。"


def test_error_event_valid_stage_is_passed_through() -> None:
    event = error_event(
        stage="main_agent",
        code="context_budget_hard",
        degraded=True,
        message="コンテキスト予算の上限に達したため打ち切りました",
    )

    assert event.data["stage"] == "main_agent"
    assert event.data["degraded"] is True


def test_resolve_error_stage_keeps_vocabulary_members() -> None:
    """M-1: 語彙内の候補(Tool 名と一致するもの)はそのまま返す。"""

    for stage in ("recommend", "plan_itinerary", "edit_itinerary", "search_knowledge"):
        assert resolve_error_stage(stage) == stage


def test_resolve_error_stage_falls_back_for_non_vocabulary_candidate() -> None:
    """M-1: 語彙外(例: "ask_user")は "main_agent" へフォールバックする。"""

    assert resolve_error_stage("ask_user") == "main_agent"
    assert resolve_error_stage("totally_unknown") == "main_agent"
