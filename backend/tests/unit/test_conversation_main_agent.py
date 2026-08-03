"""③ ReAct メインループの層 1 仕様(段2)。

`Docs/30_design/agent_react_architecture.md` §3・§10 と、段2の受け入れ条件
(単純推薦・QA が「Tool → done」の2周で完了する / R1〜R3 / ToolError の
recoverable 分岐 / state:step の発火順 / 名前解決)を検査する。
実 LLM(127.0.0.1:8000)は一切叩かない(すべてスクリプト化したモッククライアント)。
"""

from __future__ import annotations

import json
from typing import Any

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.history import estimate_tokens
from app.domains.conversation.main_agent import (
    HARD_BUDGET_TOKENS,
    MAX_EXECUTED_STEPS,
    SOFT_BUDGET_TOKENS,
    run_main_agent,
)
from app.domains.conversation.prompts import build_main_agent_messages
from app.domains.conversation.state import ProfileState, SpotFact, TurnState
from app.domains.conversation.types import ToolError, ToolErrorCode, ToolName, ToolResult


def _turn_json(tool: str, args: dict[str, Any], *, thought: str = "考える") -> str:
    return json.dumps(
        {"thought": thought, "action": {"tool": tool, "args": args}},
        ensure_ascii=False,
    )


class ScriptedMainAgentClient:
    """`generate()` を呼ぶたびに、スクリプトした応答を 1 つずつ返す。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)


class FakeTools:
    """`ConversationToolPort` を満たす、queue 方式のスクリプト化 Tool。"""

    def __init__(self) -> None:
        self.recommend_queue: list[Any] = []
        self.plan_queue: list[Any] = []
        self.edit_queue: list[Any] = []
        self.search_queue: list[Any] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def recommend(
        self, *, step_id: int, args: Any, context: Any, use_specialist: bool
    ) -> Any:
        self.calls.append(("recommend", {"step_id": step_id, "args": args}))
        return self.recommend_queue.pop(0)

    async def plan_itinerary(self, *, step_id: int, user_id: int, args: Any, **kwargs: Any) -> Any:
        self.calls.append(("plan_itinerary", {"step_id": step_id, "args": args, **kwargs}))
        return self.plan_queue.pop(0)

    async def edit_itinerary(self, *, step_id: int, user_id: int, args: Any, **kwargs: Any) -> Any:
        self.calls.append(("edit_itinerary", {"step_id": step_id, "args": args, **kwargs}))
        return self.edit_queue.pop(0)

    async def search_knowledge(self, *, step_id: int, args: Any) -> Any:
        self.calls.append(("search_knowledge", {"step_id": step_id, "args": args}))
        return self.search_queue.pop(0)

    async def ask_user(self, *, step_id: int, args: Any) -> Any:  # pragma: no cover
        raise AssertionError("段2のメインループは ask_user を呼びません")


def _spots() -> dict[str, SpotFact]:
    return {
        "spot_001": SpotFact(spot_id="spot_001", name_ja="鶴間池", kind="poi", tags_ja=["自然"]),
        "spot_002": SpotFact(spot_id="spot_002", name_ja="元滝伏流水", kind="poi", tags_ja=["滝"]),
    }


def _state() -> TurnState:
    spots = _spots()
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="滝が見たい",
        profile=ProfileState(),
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
        default_origin_spot_id="spot_001",
    )


def _recommend_result(step_id: int = 1) -> ToolResult:
    return ToolResult(
        step_id=step_id,
        tool=ToolName.RECOMMEND,
        data={
            "spot_ids": ["spot_001"],
            "candidates": [
                {
                    "spot_id": "spot_001",
                    "rank": 1,
                    "reason_materials": {
                        "matched_tags": ["自然"],
                        "travel_time_text": "車で10分",
                    },
                }
            ],
            "provisional_spot_ids": ["spot_001"],
            "rerank_used": True,
        },
    )


def _search_result(step_id: int = 1) -> ToolResult:
    return ToolResult(
        step_id=step_id,
        tool=ToolName.SEARCH_KNOWLEDGE,
        data={
            "answer_ja": "由来は江戸期の伝承です。",
            "sources": [{"kind": "knowledge", "doc_id": "d1", "title": "由来資料"}],
            "coverage": "full",
            "spot_id": None,
        },
    )


def _itinerary_payload(version: int = 1) -> dict[str, Any]:
    return {
        "days": [
            {
                "date": "2026-08-05",
                "start_min": 540,
                "end_min": 1020,
                "origin": {"kind": "spot", "spot_id": "spot_001"},
                "destination": {"kind": "spot", "spot_id": "spot_001"},
                "items": [],
            }
        ],
        "concessions": [],
        "version": version,
    }


def _plan_result(step_id: int = 1) -> ToolResult:
    return ToolResult(
        step_id=step_id,
        tool=ToolName.PLAN_ITINERARY,
        data={
            "itinerary": _itinerary_payload(),
            "alternatives": [],
            "concessions": [],
            "selection_used": False,
            "spot_ids": [],
            "unmodeled": [],
        },
    )


async def test_recommend_then_done_completes_in_two_turns() -> None:
    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [_recommend_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "滝が見たい"}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert state.main_agent_turns == 2
    assert state.executed_tool_count == 1
    assert [call[0] for call in tools.calls] == ["recommend"]
    assert len(state.trajectory) == 1
    assert state.trajectory[0].tool == "recommend"
    assert "鶴間池" in state.trajectory[0].observation
    assert "spot_001" not in state.trajectory[0].observation
    assert state.step_results[1].tool is ToolName.RECOMMEND


async def test_search_knowledge_then_done_completes_in_two_turns() -> None:
    state = _state()
    tools = FakeTools()
    tools.search_queue = [_search_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("search_knowledge", {"request": "由来を教えて", "spot_name": None}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert state.main_agent_turns == 2
    assert state.executed_tool_count == 1
    assert [call[0] for call in tools.calls] == ["search_knowledge"]
    assert "由来は江戸期の伝承です" in state.trajectory[0].observation


async def test_r1_step_budget_switches_to_done_only_schema() -> None:
    state = _state()
    tools = FakeTools()
    tools.search_queue = [_search_result(index) for index in range(1, MAX_EXECUTED_STEPS + 1)]
    responses = [
        _turn_json("search_knowledge", {"request": f"質問{index}", "spot_name": None})
        for index in range(1, MAX_EXECUTED_STEPS + 1)
    ]
    responses.append(_turn_json("done", {}))
    client = ScriptedMainAgentClient(responses)

    await run_main_agent(state, tools=tools, client=client)

    assert state.executed_tool_count == MAX_EXECUTED_STEPS
    assert state.main_agent_turns == MAX_EXECUTED_STEPS + 1
    # 9 手目(縮小スキーマ)の呼び出しは done のみを許す。
    ninth_schema = client.calls[MAX_EXECUTED_STEPS]["extra_body"]["response_format"]["json_schema"][
        "schema"
    ]
    assert ninth_schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]
    # 1〜8 手目は通常スキーマ(anyOf で5つの Tool を許す)。
    first_schema = client.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert len(first_schema["properties"]["action"]["anyOf"]) == 5


def _pad_history_to_reach(target_tokens: int) -> str:
    baseline_state = _state()
    baseline_messages = build_main_agent_messages(baseline_state, reduced=False)
    baseline_tokens = sum(estimate_tokens(value["content"]) for value in baseline_messages)
    needed = max(0, target_tokens - baseline_tokens)
    return "あ" * needed


async def test_r2_soft_budget_switches_to_reduced_schema_without_stopping() -> None:
    state = _state()
    state.history = _pad_history_to_reach(SOFT_BUDGET_TOKENS + 300)
    tools = FakeTools()
    client = ScriptedMainAgentClient([_turn_json("done", {})])

    await run_main_agent(state, tools=tools, client=client)

    assert len(client.calls) == 1
    schema = client.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]
    assert state.main_agent_failed is False


async def test_r2_hard_budget_forces_done_without_calling_llm() -> None:
    state = _state()
    state.history = _pad_history_to_reach(HARD_BUDGET_TOKENS + 500)
    tools = FakeTools()
    client = ScriptedMainAgentClient([])
    sink = MemoryEventSink()

    await run_main_agent(state, tools=tools, client=client, event_sink=sink)

    assert client.calls == []
    assert state.trajectory == []
    assert any(value.code == "context_budget_hard" for value in state.degraded)
    error_events = [event for event in sink.events if event.event == "error"]
    assert error_events[0].data["code"] == "context_budget_hard"
    assert error_events[0].data["degraded"] is True


async def test_r3_repeated_action_is_not_executed_twice() -> None:
    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [_recommend_result()]
    same_args = {"instruction": "滝が見たい"}
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", same_args),
            _turn_json("recommend", same_args),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert [call[0] for call in tools.calls] == ["recommend"]
    assert state.executed_tool_count == 1
    assert len(state.trajectory) == 2
    assert "繰り返し" in state.trajectory[1].observation


async def test_tool_error_recoverable_continues_loop() -> None:
    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [
        ToolError(
            code=ToolErrorCode.EMPTY_RESULT,
            message_ja="候補が見つかりませんでした。",
            recoverable=True,
        )
    ]
    tools.search_queue = [_search_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "滝が見たい"}),
            _turn_json("search_knowledge", {"request": "由来", "spot_name": None}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert [call[0] for call in tools.calls] == ["recommend", "search_knowledge"]
    assert state.trajectory[0].error is not None
    assert state.trajectory[0].error["recoverable"] is True
    assert state.trajectory[1].error is None
    assert state.executed_tool_count == 2


async def test_tool_error_non_recoverable_aborts_loop() -> None:
    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [
        ToolError(
            code=ToolErrorCode.INTERNAL,
            message_ja="処理中に予期しない問題が発生しました。",
            recoverable=False,
        )
    ]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "滝が見たい"}),
            _turn_json("search_knowledge", {"request": "この行は呼ばれない", "spot_name": None}),
        ]
    )
    sink = MemoryEventSink()

    await run_main_agent(state, tools=tools, client=client, event_sink=sink)

    assert [call[0] for call in tools.calls] == ["recommend"]
    assert len(client.calls) == 1
    assert state.trajectory[0].error["recoverable"] is False
    error_events = [event for event in sink.events if event.event == "error"]
    assert error_events[0].data["stage"] == "recommend"
    assert error_events[0].data["degraded"] is False


async def test_state_step_fires_started_then_finished_and_never_plan() -> None:
    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [_recommend_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "滝が見たい"}),
            _turn_json("done", {}),
        ]
    )
    sink = MemoryEventSink()

    await run_main_agent(state, tools=tools, client=client, event_sink=sink)

    kinds_and_status = [
        (event.data.get("kind"), event.data.get("status"))
        for event in sink.events
        if event.event == "state"
    ]
    assert kinds_and_status == [("step", "started"), ("step", "finished")]
    assert all(value[0] != "plan" for value in kinds_and_status)


async def test_plan_itinerary_resolves_names_and_reports_dropped() -> None:
    state = _state()
    tools = FakeTools()
    tools.plan_queue = [_plan_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "plan_itinerary",
                {
                    "days": [
                        {
                            "date": "2026-08-05",
                            "start": "09:00",
                            "end": "17:00",
                            "origin_name": None,
                            "destination_name": None,
                        }
                    ],
                    "must_visit": ["鶴間池", "架空スポット"],
                    "constraints": None,
                    "notes": None,
                },
            ),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    tool_name, call_args = tools.calls[0]
    assert tool_name == "plan_itinerary"
    assert call_args["args"].must_visit == ["spot_001"]
    assert "架空スポット" in state.trajectory[0].observation
    assert "解決できなかった" in state.trajectory[0].observation
    assert "spot_001" not in state.trajectory[0].observation
