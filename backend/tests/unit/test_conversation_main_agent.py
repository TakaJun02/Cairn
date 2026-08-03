"""③ ReAct メインループの層 1 仕様(段2〜段5)。

`Docs/30_design/agent_react_architecture.md` §3・§7・§10 と、受け入れ条件
(単純推薦・QA が「Tool → done」の2周で完了する / R1〜R4 / ToolError の
recoverable 分岐 / state:step の発火順 / 名前解決 / ask_user の HITL 接続)
を検査する。実 LLM(127.0.0.1:8000)は一切叩かない(すべてスクリプト化した
モッククライアント)。
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


def _recommend_act_json(
    *,
    filter: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
    thought: str = "条件を考える",
) -> str:
    """段3: レコメンド SA の判定 LLM(`recommend_agent`)の guided 応答。

    `run_main_agent` は `recommend` action を実行するたびに、まずこの SA を
    1 回呼んでから既存のレコメンド処理へ渡す。`ScriptedMainAgentClient` は
    メインループと SA で同じ応答キューを共有するため、`recommend` action の
    直後にはこの形の応答を 1 つ挟む。
    """

    return json.dumps(
        {
            "thought": thought,
            "action": {
                "tool": "done",
                "args": {
                    "filter": filter or {},
                    "assumptions": assumptions or [],
                },
            },
        },
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
        self.ask_queue: list[Any] = []
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

    async def search_knowledge(
        self, *, step_id: int, args: Any, ask_callback: Any = None
    ) -> Any:
        self.calls.append(
            ("search_knowledge", {"step_id": step_id, "args": args, "ask_callback": ask_callback})
        )
        return self.search_queue.pop(0)

    async def ask_user(self, *, step_id: int, args: Any) -> Any:
        self.calls.append(("ask_user", {"step_id": step_id, "args": args}))
        if self.ask_queue:
            return self.ask_queue.pop(0)
        raise AssertionError(
            "ask_user が呼ばれましたが、FakeTools.ask_queue に応答が積まれていません"
        )


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
        # レコメンド SA の guided schema/検証が読む生タグ 80 語相当(§4)。
        # テストでは実データを模した小さな語彙で十分。
        tag_vocabulary=["自然", "滝", "登山", "温泉"],
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
            _recommend_act_json(filter={"tags": ["滝"]}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    # main_agent_turns はメインループの周回だけを数える(SA の判定 LLM は
    # 別カウンタで、main_agent_turns には含まれない)。
    assert state.main_agent_turns == 2
    assert state.executed_tool_count == 1
    assert [call[0] for call in tools.calls] == ["recommend"]
    assert len(state.trajectory) == 1
    assert state.trajectory[0].tool == "recommend"
    assert "鶴間池" in state.trajectory[0].observation
    assert "spot_001" not in state.trajectory[0].observation
    assert state.step_results[1].tool is ToolName.RECOMMEND
    # SA が翻訳した filter がそのまま既存のレコメンド処理へ渡っている。
    recommend_call = next(call for call in tools.calls if call[0] == "recommend")
    assert recommend_call[1]["args"].filter == {"tags": ["滝"]}


async def test_recommend_dispatch_drops_invalid_tag_element_and_keeps_valid() -> None:
    """23_ux_issues.md §0.3 / §7-2 の実害シナリオ(C4: 要素単位で落とす)。

    「滝や湧水などの自然が好きです。車で回ります」のような指示から、SA が
    実在しないタグ(「山」)や不正な mobility(「car」— 歩行耐性ではなく
    移動手段)を返しても、その要素だけが落ち、有効な「滝」は残ったまま
    既存のレコメンド処理が実行される(0 件応答にしない)。
    """

    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [_recommend_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "滝や湧水が好きです。車で回ります"}),
            _recommend_act_json(filter={"tags": ["滝", "山"], "mobility": "car"}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    recommend_call = next(call for call in tools.calls if call[0] == "recommend")
    # 「山」はタグ語彙に無いため落ち、「滝」だけが残る。mobility の
    # "car" も enum(avoid_walk/short_walk_ok/hike_ok)に無いため落ちる。
    assert recommend_call[1]["args"].filter == {"tags": ["滝"]}
    assert "mobility" not in recommend_call[1]["args"].filter
    # 落とした事実は結果ダイジェスト(このターンの軌跡)に必ず現れる(無言破棄の禁止)。
    observation = state.trajectory[0].observation
    assert "山" in observation
    assert "car" in observation
    assert "除外した条件" in observation


async def test_recommend_dispatch_executes_with_empty_filter_when_all_elements_invalid() -> None:
    """C4: 全要素が不正でも filter なしで実行し、0 件応答にしない。"""

    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [_recommend_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "よくわからないけど何か教えて"}),
            _recommend_act_json(filter={"tags": ["架空タグ"], "mobility": "car"}),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    recommend_call = next(call for call in tools.calls if call[0] == "recommend")
    # tags/mobility とも全滅しても、生き残った要素だけの filter(= 実質
    # 絞り込み無し)になり、tools.recommend は実行される(手ごと破棄されない)。
    assert recommend_call[1]["args"].filter == {"tags": []}
    assert [call[0] for call in tools.calls] == ["recommend"]
    assert state.trajectory[0].error is None


async def test_recommend_dispatch_reports_assumptions_in_digest() -> None:
    """A7: 質問できない場面では仮定して推薦し、置いた仮定を結果で報告する。"""

    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [_recommend_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "おすすめを教えて"}),
            _recommend_act_json(
                filter={},
                assumptions=["同行者の情報が無いため、対象者は絞りませんでした"],
            ),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    observation = state.trajectory[0].observation
    assert "置いた仮定" in observation
    assert "同行者の情報が無いため、対象者は絞りませんでした" in observation


async def test_recommend_dispatch_falls_back_to_no_filter_when_subagent_call_fails() -> None:
    """SA の判定 LLM 呼び出し自体が壊れても、推薦そのものは止めない(NFR-5)。"""

    state = _state()
    tools = FakeTools()
    tools.recommend_queue = [_recommend_result()]
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "おすすめを教えて"}),
            "not a json",
            "not a json",  # 再試行後も契約違反 → SA はフォールバックする
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    recommend_call = next(call for call in tools.calls if call[0] == "recommend")
    assert recommend_call[1]["args"].filter == {"tags": []}
    assert any(value.code == "recommend_agent_degraded" for value in state.degraded)


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
    # 1〜8 手目は通常スキーマ(anyOf で6つの Tool を許す。ask_user 含む)。
    first_schema = client.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert len(first_schema["properties"]["action"]["anyOf"]) == 6


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
            _recommend_act_json(),
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
            _recommend_act_json(),
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
            _recommend_act_json(),
            _turn_json("search_knowledge", {"request": "この行は呼ばれない", "spot_name": None}),
        ]
    )
    sink = MemoryEventSink()

    await run_main_agent(state, tools=tools, client=client, event_sink=sink)

    assert [call[0] for call in tools.calls] == ["recommend"]
    # 1回目はメインループの action 選択、2回目はレコメンド SA の判定 LLM。
    # ToolError(非回復)でループが打ち切られるため、3個目(search_knowledge 用)
    # は消費されない。
    assert len(client.calls) == 2
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
            _recommend_act_json(),
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
    # started(メイン) → progress(レコメンド SA の判定 LLM 実行中) → finished。
    assert kinds_and_status == [("step", "started"), ("step", "progress"), ("step", "finished")]
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


# ---------------------------------------------------------------------------
# ask_user(§7): メインエージェント自身の HITL 接続
# ---------------------------------------------------------------------------


def _ask_user_action(
    *,
    kind: str,
    slot: str | None = None,
    surface: str | None = None,
    reason: str = "確認させてください",
    options: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "slot": slot,
        "surface": surface,
        "reason": reason,
        "options": options
        or [
            {"label": "はい", "value": "yes"},
            {"label": "いいえ", "value": "no"},
        ],
    }


def _update_profile_json(*, mobility: str | None = None) -> str:
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


def _ask_result(*, answer: str, answered_by: str = "chip") -> ToolResult:
    return ToolResult(
        step_id=1, tool=ToolName.ASK_USER, data={"answer": answer, "answered_by": answered_by}
    )


async def test_ask_user_preference_answer_updates_profile_and_continues_to_done() -> None:
    """メイン: ask_user → 回答 → update_profile 再実行 → ループ続行 → done。"""

    state = _state()
    tools = FakeTools()
    tools.ask_queue = [_ask_result(answer="30分程度なら")]
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "ask_user",
                _ask_user_action(
                    kind="preference",
                    slot="mobility",
                    reason="どのくらい歩けますか",
                    options=[
                        {"label": "あまり歩きたくない", "value": "avoid_walk"},
                        {"label": "30分程度なら", "value": "short_walk_ok"},
                    ],
                ),
            ),
            _update_profile_json(mobility="short_walk_ok"),  # execute_ask_user の再実行分
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert [call[0] for call in tools.calls] == ["ask_user"]
    assert state.ask_user_count == 1
    assert state.asked_slots == ["mobility"]
    assert state.profile.mobility == "short_walk_ok"
    assert state.qa_answers[0]["answer"] == "30分程度なら"
    assert "30分程度なら" in state.trajectory[0].observation
    assert state.trajectory[0].tool == "ask_user"
    assert state.main_agent_turns == 2  # ①ask_user ②done(done は軌跡に載らない)


async def test_ask_user_clarify_resolves_spot_names_to_ids_before_dispatch() -> None:
    """§3.3: メインは spot_id を書かない。options[].value は名前で渡す。"""

    state = _state()
    tools = FakeTools()
    tools.ask_queue = [_ask_result(answer="鶴間池")]
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "ask_user",
                _ask_user_action(
                    kind="clarify",
                    surface="2番目のやつ",
                    reason="候補が 2 つあります",
                    options=[
                        {"label": "鶴間池", "value": "鶴間池"},
                        {"label": "元滝伏流水", "value": "元滝伏流水"},
                    ],
                ),
            ),
            _update_profile_json(),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    ask_call = next(call for call in tools.calls if call[0] == "ask_user")
    resolved_values = [option.value for option in ask_call[1]["args"].options]
    assert resolved_values == ["spot_001", "spot_002"]
    assert state.resolved_ambiguities == [
        {"surface": "2番目のやつ", "resolved_to": "spot_001"}
    ]


async def test_ask_user_clarify_with_unresolvable_name_is_not_executed() -> None:
    """A4: 選択肢が具体値(spot_id)に解決できない質問は実行せず落とす。"""

    state = _state()
    tools = FakeTools()
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "ask_user",
                _ask_user_action(
                    kind="clarify",
                    surface="どこか",
                    reason="どちらのことですか",
                    options=[
                        {"label": "鶴間池", "value": "鶴間池"},
                        {"label": "架空スポット", "value": "架空スポット"},
                    ],
                ),
            ),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert tools.calls == []  # 実行されない(ガード前に名前解決で落ちる)
    assert "解決" in state.trajectory[0].observation
    assert state.ask_user_count == 0


async def test_ask_user_guard_rejection_reports_reason_and_continues() -> None:
    """A1: 質問済み slot への再質問はガードで落ち、仮定して進める指示が残る。"""

    state = _state()
    state.asked_slots = ["mobility"]
    tools = FakeTools()
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "ask_user",
                _ask_user_action(
                    kind="preference",
                    slot="mobility",
                    reason="どのくらい歩けますか",
                ),
            ),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert tools.calls == []
    assert "質問できませんでした" in state.trajectory[0].observation
    assert "最も確からしい解釈" in state.trajectory[0].observation
    assert state.ask_user_count == 0


async def test_ask_user_r4_limit_removes_ask_user_from_schema_after_two_questions() -> None:
    """R4: 1 ターンに ask_user は 2 回まで。3 周目のスキーマから外れる。"""

    state = _state()
    tools = FakeTools()
    tools.ask_queue = [
        _ask_result(answer="30分程度なら"),
        _ask_result(answer="家族です"),
    ]
    client = ScriptedMainAgentClient(
        [
            _turn_json(
                "ask_user",
                _ask_user_action(kind="preference", slot="mobility", reason="歩けますか"),
            ),
            _update_profile_json(),
            _turn_json(
                "ask_user",
                _ask_user_action(kind="preference", slot="party", reason="どなたと"),
            ),
            _update_profile_json(),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert state.ask_user_count == 2
    assert state.main_agent_turns == 3  # ①ask_user ②ask_user ③done

    # client.calls には update_profile 再実行分も混ざるので、
    # メインループ自身の周(schema に "action" プロパティを持つ)だけを拾う。
    main_loop_schemas = [
        call["extra_body"]["response_format"]["json_schema"]["schema"]
        for call in client.calls
        if "action" in call["extra_body"]["response_format"]["json_schema"]["schema"]["properties"]
    ]
    assert len(main_loop_schemas) == 3
    third_schema = main_loop_schemas[2]
    third_tools = [
        branch["properties"]["tool"]["enum"][0]
        for branch in third_schema["properties"]["action"]["anyOf"]
    ]
    assert "ask_user" not in third_tools
    first_tools = [
        branch["properties"]["tool"]["enum"][0]
        for branch in main_loop_schemas[0]["properties"]["action"]["anyOf"]
    ]
    assert "ask_user" in first_tools
