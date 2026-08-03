"""load_context → update_profile → main_agent → respond → persist を

end-to-end でモック LLM ・モック Tool を使って検査する(段2)。

受け入れ条件: 単純推薦(recommend → done)と QA(search_knowledge → done)の
ターンが新しい ReAct ループで完走すること。実 LLM(127.0.0.1:8000)は
一切叩かない。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.domains.conversation.events import MemoryEventSink, emit, state_event
from app.domains.conversation.pipeline import ConversationPipeline
from app.domains.conversation.state import (
    ContextSnapshot,
    ProfileState,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import ToolError, ToolErrorCode, ToolName, ToolResult


def _turn_json(tool: str, args: dict[str, Any], *, thought: str = "考える") -> str:
    return json.dumps(
        {"thought": thought, "action": {"tool": tool, "args": args}},
        ensure_ascii=False,
    )


def _done_json() -> str:
    return _turn_json("done", {})


def _recommend_act_json(
    *, filter: dict[str, Any] | None = None, assumptions: list[str] | None = None
) -> str:
    """段3: レコメンド SA(`recommend_agent.run_recommend_subagent`)の guided 応答。

    `recommend` action を実行するたびに、既存のレコメンド処理の前に SA の
    判定 LLM が 1 回挟まる。`TurnClient` は update_profile/main_agent/SA の
    区別なく `generate()` 呼び出し順にキューを消費するため、`recommend`
    action の直後にはこの形の応答を 1 つ挟む。
    """

    return _turn_json("done", {"filter": filter or {}, "assumptions": assumptions or []})


def _update_profile_output(
    *,
    profile_delta: dict[str, Any] | None = None,
    score_adjustments: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {"profile_delta": profile_delta, "score_adjustments": score_adjustments or []},
        ensure_ascii=False,
    )


class TurnClient:
    """update_profile → main_agent(複数回)の順で消費される generate() 応答キュー。"""

    def __init__(
        self,
        generate_responses: list[str | BaseException],
        *,
        response_chunks: list[str] | None = None,
        response_error: BaseException | None = None,
    ) -> None:
        self.generate_responses = list(generate_responses)
        self.response_chunks = response_chunks or []
        self.response_error = response_error
        self.generate_calls: list[list[dict[str, str]]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del kwargs
        self.generate_calls.append(messages)
        response = self.generate_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any):
        del kwargs
        self.generate_calls.append(messages)
        for value in self.response_chunks:
            yield value
        if self.response_error is not None:
            raise self.response_error


class MemoryConversationRepository:
    def __init__(self) -> None:
        spot = SpotFact(
            spot_id="spot_001",
            name_ja="鶴間池",
            kind="facility",
            aliases_ja=["つるまいけ"],
            tags_ja=["自然"],
        )
        self.snapshot = ContextSnapshot(
            thread_id=1,
            profile=ProfileState(),
            itinerary=None,
            messages=[],
            last_candidates=[],
            presented_spot_ids=[],
            asked_slots=[],
            ask_streak=0,
            pending_ask=None,
            resolved_ambiguities=[],
            pending_constraints=[],
            realtime={},
            spots={spot.spot_id: spot},
        )
        self.persisted: list[TurnState] = []

    async def load_snapshot(self, user_id: int) -> ContextSnapshot:
        assert user_id == 1
        return self.snapshot.model_copy(deep=True)

    async def persist_turn(self, state: TurnState) -> int | None:
        self.persisted.append(state.model_copy(deep=True))
        return 88 if state.responded else None


class FakeTools:
    def __init__(self, *, sink: MemoryEventSink | None = None) -> None:
        self.sink = sink
        self.recommend_queue: list[Any] = []
        self.plan_queue: list[Any] = []
        self.edit_queue: list[Any] = []
        self.search_queue: list[Any] = []
        self.ask_queue: list[Any] = []
        self.calls: list[str] = []
        # narration の ask コールバック(port)の統合テスト用: 設定すると
        # search_knowledge が渡された ask_callback を実際に 1 回呼ぶ
        # (§6: search_knowledge SA が内部で ask_user を選んだことを模す)。
        self.invoke_ask_callback_with: dict[str, Any] | None = None
        self.ask_callback_observations: list[str] = []

    async def recommend(
        self, *, step_id: int, args: Any, context: Any, use_specialist: bool
    ) -> Any:
        del args, context, use_specialist
        self.calls.append("recommend")
        if self.sink is not None:
            await emit(
                self.sink,
                state_event(
                    "candidates",
                    phase="provisional",
                    items=[{"spot_id": "spot_001", "name_ja": "鶴間池"}],
                ),
            )
            await emit(
                self.sink,
                state_event(
                    "candidates",
                    phase="final",
                    items=[{"spot_id": "spot_001", "name_ja": "鶴間池"}],
                ),
            )
        if self.recommend_queue:
            return self.recommend_queue.pop(0)
        return ToolResult(
            step_id=step_id,
            tool=ToolName.RECOMMEND,
            data={
                "spot_ids": ["spot_001"],
                "candidates": [{"spot_id": "spot_001", "rank": 1, "reason_materials": {}}],
                "provisional_spot_ids": ["spot_001"],
                "rerank_used": False,
            },
        )

    async def plan_itinerary(self, *, step_id: int, user_id: int, args: Any, **kwargs: Any) -> Any:
        del user_id, args, kwargs
        self.calls.append("plan_itinerary")
        return self.plan_queue.pop(0)

    async def edit_itinerary(self, *, step_id: int, user_id: int, args: Any, **kwargs: Any) -> Any:
        del user_id, args, kwargs
        self.calls.append("edit_itinerary")
        return self.edit_queue.pop(0)

    async def search_knowledge(
        self, *, step_id: int, args: Any, ask_callback: Any = None
    ) -> Any:
        del args
        self.calls.append("search_knowledge")
        if self.invoke_ask_callback_with is not None and ask_callback is not None:
            observation = await ask_callback(**self.invoke_ask_callback_with)
            self.ask_callback_observations.append(observation)
        if self.search_queue:
            return self.search_queue.pop(0)
        return ToolResult(
            step_id=step_id,
            tool=ToolName.SEARCH_KNOWLEDGE,
            data={"answer_ja": "資料の回答", "sources": [], "coverage": "none", "spot_id": None},
        )

    async def ask_user(self, *, step_id: int, args: Any) -> Any:
        del args
        self.calls.append("ask_user")
        if self.ask_queue:
            return self.ask_queue.pop(0)
        raise AssertionError(
            "ask_user が呼ばれましたが、FakeTools.ask_queue に応答が積まれていません"
        )


async def test_simple_recommend_turn_completes_end_to_end() -> None:
    """受け入れ条件: recommend → done の単純推薦が新ループで完走する。"""

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json("recommend", {"instruction": "滝が見たい"}),
            _recommend_act_json(),
            _done_json(),
        ],
        response_chunks=["鶴間池をご案内します。", "今回考慮した条件: なし。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="おすすめは？")

    assert tools.calls == ["recommend"]
    assert repository.persisted[0].assistant_text == state.assistant_text
    assert repository.persisted[0].responded is True
    assert [event.event for event in sink.events] == [
        "state",
        "state",
        "state",
        "state",
        "state",
        "token",
        "token",
        "done",
    ]
    kinds = [event.data.get("kind") for event in sink.events if event.event == "state"]
    # started(メイン) → progress(レコメンド SA の判定 LLM 実行中) →
    # candidates(provisional/final) → finished(メイン)。
    assert kinds == ["step", "step", "candidates", "candidates", "step"]
    assert sink.events[0].data == {
        "kind": "step",
        "tool": "recommend",
        "status": "started",
        "label_ja": "おすすめを探しています",
    }
    assert sink.events[-1].data["message_id"] == 88


async def test_qa_turn_completes_end_to_end() -> None:
    """受け入れ条件: search_knowledge → done の QA が新ループで完走する。"""

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json("search_knowledge", {"request": "由来を教えて", "spot_name": None}),
            _done_json(),
        ],
        response_chunks=["由来をご説明します。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="鶴間池の由来は？")

    assert tools.calls == ["search_knowledge"]
    assert state.respond_status == "complete"
    kinds_and_status = [
        (event.data.get("kind"), event.data.get("status"))
        for event in sink.events
        if event.event == "state"
    ]
    assert kinds_and_status == [("step", "started"), ("step", "finished")]


async def test_respond_input_includes_this_turns_trajectory() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json("recommend", {"instruction": "滝が見たい"}),
            _recommend_act_json(),
            _done_json(),
        ],
        response_chunks=["鶴間池をご案内します。"],
    )

    await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="おすすめは？")

    # generate_calls: [update_profile, main_agent#1(recommend action),
    # レコメンド SA の判定 LLM, main_agent#2(done)] → stream() は別カウント
    # なので最後の呼び出しは main_agent#2(done)のメッセージになる。
    respond_messages = client.generate_calls[-1]
    respond_dynamic = respond_messages[1]["content"]
    assert "tool=recommend" in respond_dynamic


async def test_non_recoverable_tool_error_still_reaches_persist_and_done() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    tools.recommend_queue = [
        ToolError(
            code=ToolErrorCode.INTERNAL,
            message_ja="処理中に予期しない問題が発生しました。",
            recoverable=False,
        )
    ]
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json("recommend", {"instruction": "滝が見たい"}),
            _recommend_act_json(),
        ],
        response_chunks=["申し訳ありません、処理できませんでした。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="おすすめは？")

    assert len(repository.persisted) == 1
    assert repository.persisted[0].responded is True
    assert [event.event for event in sink.events][-1] == "done"
    assert any(event.event == "error" for event in sink.events)
    assert state.degraded


async def test_respond_failure_still_persists_failed_assistant_and_done() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    client = TurnClient(
        [_update_profile_output(), _done_json()],
        response_error=RuntimeError("stream down"),
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=FakeTools(),
    ).run(user_id=1, utterance="こんにちは")

    assert state.respond_status == "failed"
    assert len(repository.persisted) == 1
    assert repository.persisted[0].respond_status == "failed"
    assert repository.persisted[0].responded is True
    assert [event.event for event in sink.events][-2:] == ["error", "done"]


async def test_main_agent_total_failure_still_responds_in_failure_mode() -> None:
    """guided JSON が確定できなくても respond まで到達し、assistant 行を残す。"""

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    client = TurnClient(
        [_update_profile_output(), "bad json", "bad json again"],
        response_chunks=["うまく処理できませんでした。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=FakeTools(),
    ).run(user_id=1, utterance="理解不能")

    assert state.main_agent_failed is True
    assert state.responded is True
    assert len(repository.persisted) == 1
    assert repository.persisted[0].responded is True
    assert any(event.event == "error" for event in sink.events)
    assert [event.event for event in sink.events][-1] == "done"


async def test_cancelled_main_agent_persists_user_only_before_propagating() -> None:
    """respond に到達していないので assistant 行は書かない(§13: persist には必ず到達)。"""

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()

    with pytest.raises(asyncio.CancelledError):
        await ConversationPipeline(
            repository,
            event_sink=sink,
            llm_client=TurnClient([_update_profile_output(), asyncio.CancelledError()]),
            tools=FakeTools(),
        ).run(user_id=1, utterance="途中で停止")

    assert len(repository.persisted) == 1
    assert repository.persisted[0].responded is False
    assert sink.events[-1].event == "done"


async def test_cancelled_update_profile_persists_user_only_before_propagating() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()

    with pytest.raises(asyncio.CancelledError):
        await ConversationPipeline(
            repository,
            event_sink=sink,
            llm_client=TurnClient([asyncio.CancelledError()]),
            tools=FakeTools(),
        ).run(user_id=1, utterance="途中で停止")

    assert len(repository.persisted) == 1
    assert repository.persisted[0].responded is False
    assert sink.events[-1].event == "done"


async def test_cancelled_respond_persists_partial_text_before_propagating() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    client = TurnClient(
        [_update_profile_output(), _done_json()],
        response_chunks=["生成途中"],
        response_error=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        await ConversationPipeline(
            repository,
            event_sink=sink,
            llm_client=client,
            tools=FakeTools(),
        ).run(user_id=1, utterance="こんにちは")

    assert len(repository.persisted) == 1
    assert repository.persisted[0].assistant_text == "生成途中"
    assert repository.persisted[0].respond_status == "partial"
    assert repository.persisted[0].responded is True
    assert sink.events[-1].event == "done"


# ---------------------------------------------------------------------------
# 受け入れ条件(段5): 3 経路それぞれで
# 「質問 → 回答 → 同一ターン続行 → done → respond」が通ること
# ---------------------------------------------------------------------------


async def test_ask_user_timeout_still_continues_to_done_and_respond() -> None:
    """§7: タイムアウトでも `answered_by:"timeout"` で続行し respond まで到達する。"""

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    tools.ask_queue = [
        ToolResult(
            step_id=1,
            tool=ToolName.ASK_USER,
            data={
                "answer": "(タイムアウトのため回答がありませんでした)",
                "answered_by": "timeout",
            },
        )
    ]
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json(
                "ask_user",
                {
                    "kind": "preference",
                    "slot": "mobility",
                    "surface": None,
                    "reason": "どのくらい歩けますか",
                    "options": [
                        {"label": "あまり歩きたくない", "value": "avoid_walk"},
                        {"label": "30分程度なら", "value": "short_walk_ok"},
                    ],
                },
            ),
            _update_profile_output(),  # 未回答なので profile_delta は空
            _done_json(),
        ],
        response_chunks=["回答が確認できなかったため、仮定して進めます。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="おすすめは？")

    assert state.responded is True
    assert state.ask_user_count == 1
    assert state.qa_answers[0]["meta"]["answered_by"] == "timeout"
    assert repository.persisted[0].responded is True
    assert [event.event for event in sink.events][-1] == "done"


async def test_main_agent_ask_user_round_trip_reaches_done_and_respond() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    tools.ask_queue = [
        ToolResult(
            step_id=1,
            tool=ToolName.ASK_USER,
            data={"answer": "30分程度なら", "answered_by": "chip"},
        )
    ]
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json(
                "ask_user",
                {
                    "kind": "preference",
                    "slot": "mobility",
                    "surface": None,
                    "reason": "どのくらい歩けますか",
                    "options": [
                        {"label": "あまり歩きたくない", "value": "avoid_walk"},
                        {"label": "30分程度なら", "value": "short_walk_ok"},
                    ],
                },
            ),
            _update_profile_output(
                profile_delta={
                    "interests": {},
                    "party": None,
                    "mobility": "short_walk_ok",
                    "pace": None,
                    "avoid": [],
                    "notes": None,
                }
            ),
            _done_json(),
        ],
        response_chunks=["30分程度歩ける前提でご案内します。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="おすすめは？")

    assert state.responded is True
    assert state.profile.mobility == "short_walk_ok"
    assert state.ask_user_count == 1
    assert repository.persisted[0].responded is True
    assert repository.persisted[0].qa_answers[0]["answer"] == "30分程度なら"
    assert [event.event for event in sink.events][-1] == "done"


async def test_recommend_subagent_ask_user_round_trip_reaches_done_and_respond() -> None:
    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    tools.ask_queue = [
        ToolResult(
            step_id=1,
            tool=ToolName.ASK_USER,
            data={"answer": "家族です", "answered_by": "chip"},
        )
    ]
    ask_action = {
        "slot": "party",
        "reason": "どなたと行かれますか",
        "options": [
            {"label": "家族", "value": "family_kids"},
            {"label": "一人", "value": "solo"},
        ],
    }
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json("recommend", {"instruction": "おすすめを教えて"}),
            _turn_json("ask_user", ask_action),  # レコメンド SA 自身の判定(§4)
            _update_profile_output(
                profile_delta={
                    "interests": {},
                    "party": "family_kids",
                    "mobility": None,
                    "pace": None,
                    "avoid": [],
                    "notes": None,
                }
            ),
            _recommend_act_json(filter={"tags": ["自然"]}),
            _done_json(),
        ],
        response_chunks=["家族向けにご案内します。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="おすすめは？")

    assert state.responded is True
    assert state.profile.party == "family_kids"
    assert tools.calls.count("recommend") == 1
    assert repository.persisted[0].responded is True


async def test_search_knowledge_ask_callback_round_trip_reaches_done_and_respond() -> None:
    """§6: narration の ask コールバック(port)経由でも同じターンが続く。"""

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    tools.invoke_ask_callback_with = {
        "kind": "clarify",
        "slot": None,
        "surface": "どちらの池",
        "reason": "候補が複数あります",
        "options": [
            {"label": "鶴間池", "value": "鶴間池"},
            {"label": "元滝伏流水", "value": "元滝伏流水"},
        ],
    }
    tools.ask_queue = [
        ToolResult(
            step_id=1,
            tool=ToolName.ASK_USER,
            data={"answer": "鶴間池", "answered_by": "chip"},
        )
    ]
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json("search_knowledge", {"request": "由来を教えて", "spot_name": None}),
            _done_json(),
        ],
        response_chunks=["鶴間池についてご説明します。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="由来は？")

    assert state.responded is True
    assert tools.ask_callback_observations
    assert "鶴間池" in tools.ask_callback_observations[0]
    assert repository.persisted[0].qa_answers[0]["answer"] == "鶴間池"


async def test_search_knowledge_ask_callback_supports_preference_kind_too() -> None:
    """narration_qa.md §2: ask_user の引数は `{kind,slot?/surface?,...}` の

    メイン/レコメンド SA と同じ形。knowledge SA も kind=preference を選べる。
    """

    sink = MemoryEventSink()
    repository = MemoryConversationRepository()
    tools = FakeTools(sink=sink)
    tools.invoke_ask_callback_with = {
        "kind": "preference",
        "slot": "mobility",
        "surface": None,
        "reason": "どのくらい歩けますか",
        "options": [
            {"label": "あまり歩きたくない", "value": "avoid_walk"},
            {"label": "30分程度なら", "value": "short_walk_ok"},
        ],
    }
    tools.ask_queue = [
        ToolResult(
            step_id=1,
            tool=ToolName.ASK_USER,
            data={"answer": "30分程度なら", "answered_by": "chip"},
        )
    ]
    client = TurnClient(
        [
            _update_profile_output(),
            _turn_json("search_knowledge", {"request": "由来を教えて", "spot_name": None}),
            _done_json(),
        ],
        response_chunks=["ご案内します。"],
    )

    state = await ConversationPipeline(
        repository,
        event_sink=sink,
        llm_client=client,
        tools=tools,
    ).run(user_id=1, utterance="由来は？")

    assert state.responded is True
    assert tools.ask_callback_observations
    assert "30分程度なら" in tools.ask_callback_observations[0]
    # knowledge SA の聞き返しは update_profile を回さない(§6)ので、
    # プロフィールへは反映されない。
    assert state.profile.mobility is None
    assert repository.persisted[0].responded is True
