"""`ask_execution.execute_ask_user` の層 1 仕様(§7・§10)。

メイン・レコメンド SA・知識検索 SA の 3 経路が共有する、ガード検査 →
`tools.ask_user`(HITL 待ち受け)→ 状態更新 → `update_profile` 再実行、
という一連の流れを検査する。実 LLM は叩かない(すべてスクリプト化)。
"""

from __future__ import annotations

import json
from typing import Any

from app.domains.conversation.ask_execution import execute_ask_user
from app.domains.conversation.state import ProfileState, TurnState
from app.domains.conversation.types import (
    AskUserArgs,
    ToolError,
    ToolErrorCode,
    ToolName,
    ToolResult,
)


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del kwargs
        self.calls.append(messages)
        return self.responses.pop(0)


class FakeAskTools:
    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    async def ask_user(self, *, step_id: int, args: AskUserArgs) -> Any:
        self.calls.append({"step_id": step_id, "args": args})
        return self.queue.pop(0)

    # 未使用(ConversationToolPort の残りのメソッド)だが Protocol を満たす。
    async def recommend(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("使いません")

    async def plan_itinerary(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("使いません")

    async def edit_itinerary(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("使いません")

    async def search_knowledge(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("使いません")


def _state(**overrides: Any) -> TurnState:
    base = dict(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="鶴間池に行きたい",
        profile=ProfileState(),
    )
    base.update(overrides)
    return TurnState(**base)


def _preference_question(slot: str = "mobility") -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "preference",
            "slot": slot,
            "reason": "どのくらい歩けますか",
            "options": [
                {"label": "あまり歩きたくない", "value": "avoid_walk"},
                {"label": "30分程度なら", "value": "short_walk_ok"},
            ],
        }
    )


def _clarify_question() -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "clarify",
            "surface": "2番目のやつ",
            "reason": "候補が 2 つあります",
            "options": [
                {"label": "鶴間池", "value": "spot_001"},
                {"label": "元滝伏流水", "value": "spot_002"},
            ],
        }
    )


def _ask_result(*, answer: str, answered_by: str) -> ToolResult:
    return ToolResult(
        step_id=1,
        tool=ToolName.ASK_USER,
        data={"answer": answer, "answered_by": answered_by},
    )


def _update_profile_json(mobility: str | None = None) -> str:
    delta = None
    if mobility is not None:
        delta = {
            "interests": {},
            "party": None,
            "mobility": mobility,
            "pace": None,
            "avoid": [],
            "notes": None,
        }
    return json.dumps({"profile_delta": delta, "score_adjustments": []}, ensure_ascii=False)


async def test_successful_preference_answer_updates_state_and_reruns_update_profile() -> None:
    state = _state()
    tools = FakeAskTools()
    tools.queue = [_ask_result(answer="30分程度なら", answered_by="chip")]
    client = ScriptedClient([_update_profile_json(mobility="short_walk_ok")])

    outcome = await execute_ask_user(
        state, tools, _preference_question(), step_id=1, client=client
    )

    assert outcome.executed is True
    assert outcome.answer_text == "30分程度なら"
    assert outcome.answered_by == "chip"
    assert state.ask_user_count == 1
    assert state.asked_slots == ["mobility"]
    assert state.profile.mobility == "short_walk_ok"
    assert state.qa_answers == [
        {
            "answer": "30分程度なら",
            "meta": {
                "answer_to": {
                    "kind": "preference",
                    "slot": "mobility",
                    "reason": "どのくらい歩けますか",
                    "options": [
                        {"label": "あまり歩きたくない", "value": "avoid_walk"},
                        {"label": "30分程度なら", "value": "short_walk_ok"},
                    ],
                },
                "answered_by": "chip",
            },
        }
    ]
    # update_profile はターンの元発話ではなく回答文を入力にする。
    assert "30分程度なら" in client.calls[0][1]["content"]
    assert "鶴間池に行きたい" not in client.calls[0][1]["content"].split("④")[-1]
    # 裁定13: 再実行プロンプトには質問文も含める(dates/origin のような
    # ProfileDelta に無いフィールドの回答も、質問文とセットなら文脈から
    # LLM が読み取れるようにするため)。
    assert "どのくらい歩けますか" in client.calls[0][1]["content"].split("④")[-1]


async def test_successful_clarify_answer_records_resolved_ambiguity() -> None:
    state = _state()
    tools = FakeAskTools()
    tools.queue = [_ask_result(answer="鶴間池", answered_by="chip")]
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _clarify_question(), step_id=1, client=client
    )

    assert outcome.executed is True
    assert outcome.resolved_spot_id == "spot_001"
    assert state.resolved_ambiguities == [{"surface": "2番目のやつ", "resolved_to": "spot_001"}]
    # 裁定13: clarify は update_profile を回さない(main の preference だけが
    # 対象)。曖昧参照の選択を恒久的選好と誤認しない。
    assert client.calls == []


async def test_timeout_answer_continues_without_writing_user_row_or_update_profile() -> None:
    """裁定12(2026-08-04レビュー是正): timeout は「未回答」として扱う。

    (a) user 行(`qa_answers`)を書かない (b) `resolved_ambiguities` に
    登録しない (c) `update_profile` を回さない (d) 軌跡には
    「未回答(タイムアウト)。仮定して進めよ」を observation として返す。

    旧実装はタイムアウトを架空のユーザー発話として `qa_answers` に書き、
    `update_profile` を再実行していた(誤実装をテストが仕様として固定して
    いた。レビュー指摘により是正)。
    """

    state = _state()
    tools = FakeAskTools()
    tools.queue = [
        _ask_result(answer="(タイムアウトのため回答がありませんでした)", answered_by="timeout")
    ]
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _preference_question(), step_id=1, client=client
    )

    assert outcome.executed is True
    assert outcome.answered_by == "timeout"
    assert outcome.answer_text is None
    assert "未回答" in outcome.digest
    assert "仮定" in outcome.digest
    # R4/A1 のカウンタ・slot 記録は「質問を提示した」こと自体には効く
    # (聞き直しをタイムアウトのたびに許さないため)。
    assert state.ask_user_count == 1
    assert state.asked_slots == ["mobility"]
    assert state.qa_answers == []
    assert state.resolved_ambiguities == []
    assert client.calls == []  # update_profile は再実行されない


async def test_timeout_clarify_answer_does_not_register_resolved_ambiguity() -> None:
    state = _state()
    tools = FakeAskTools()
    tools.queue = [_ask_result(answer="(タイムアウトのため回答がありませんでした)", answered_by="timeout")]
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _clarify_question(), step_id=1, client=client
    )

    assert outcome.executed is True
    assert outcome.answered_by == "timeout"
    assert state.qa_answers == []
    assert state.resolved_ambiguities == []
    assert client.calls == []


async def test_tool_error_from_ask_user_is_not_executed() -> None:
    state = _state()
    tools = FakeAskTools()
    tools.queue = [
        ToolError(
            code=ToolErrorCode.INTERNAL,
            message_ja="内部エラー",
            recoverable=False,
        )
    ]
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _preference_question(), step_id=1, client=client
    )

    assert outcome.executed is False
    assert outcome.error is not None
    assert state.ask_user_count == 0
    assert client.calls == []  # update_profile は再実行されない


async def test_ask_timed_out_backstop_blocks_execution_without_calling_tools() -> None:
    """H-1 残穴(2026-08-06 レビュー是正、ADR-0024)の実行時バックストップ。

    `evaluate_ask_user`(ガード検査)より前で `state.ask_timed_out` を検査
    する。メイン・レコメンド SA は schema 除外(主防御)で通常ここに到達
    しないが、知識検索 SA は自身の内部ループの `ask_callback` 可否をターン
    開始時に固定するため、同一 `search_knowledge` 呼び出し内でタイムアウト
    後に再度 `ask_user` が選ばれる余地があった。`execute_ask_user` は 3 経路
    すべてが必ず通るチョークポイントなので、ここでの防御が全経路に効く。
    """

    state = _state(ask_timed_out=True)
    tools = FakeAskTools()
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _preference_question(), step_id=1, client=client
    )

    assert outcome.executed is False
    assert outcome.guard_rule == "H1"
    assert "タイムアウト済み" in outcome.digest
    assert "最も確からしい解釈" in outcome.digest
    assert tools.calls == []  # 質問は提示されない
    assert client.calls == []  # update_profile も回らない
    # カウンタ・状態は不変(R4/A1 の消費が起きない)。
    assert state.ask_user_count == 0
    assert state.asked_slots == []


async def test_r4_guard_blocks_execution_without_calling_tools() -> None:
    state = _state(ask_user_count=6)
    tools = FakeAskTools()
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _preference_question(), step_id=1, client=client
    )

    assert outcome.executed is False
    assert outcome.guard_rule == "R4"
    assert "聞けなかった" not in outcome.digest  # 具体的な文言は仮定して進める指示を含む
    assert "最も確からしい解釈" in outcome.digest
    assert tools.calls == []
    assert client.calls == []


async def test_a1_guard_blocks_repeated_slot_question() -> None:
    state = _state(asked_slots=["mobility"])
    tools = FakeAskTools()
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _preference_question(), step_id=1, client=client
    )

    assert outcome.executed is False
    assert outcome.guard_rule == "A1"


async def test_a5_guard_blocks_repeated_ambiguity() -> None:
    state = _state(resolved_ambiguities=[{"surface": "2番目のやつ", "resolved_to": "spot_001"}])
    tools = FakeAskTools()
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state, tools, _clarify_question(), step_id=1, client=client
    )

    assert outcome.executed is False
    assert outcome.guard_rule == "A5"


async def test_run_update_profile_false_skips_the_rerun() -> None:
    """§6: 知識検索 SA の聞き返しは update_profile を回さない。"""

    state = _state()
    tools = FakeAskTools()
    tools.queue = [_ask_result(answer="鶴間池", answered_by="chip")]
    client = ScriptedClient([])

    outcome = await execute_ask_user(
        state,
        tools,
        _clarify_question(),
        step_id=1,
        client=client,
        run_update_profile=False,
    )

    assert outcome.executed is True
    assert client.calls == []
