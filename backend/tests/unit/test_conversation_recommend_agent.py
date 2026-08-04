"""④ レコメンドサブエージェントの層 1 仕様(段3)。

`Docs/30_design/agent_react_architecture.md` §4 と、23_ux_issues.md §0.3・
§7-2(手ごと破棄の再発防止)の受け入れ条件を検査する。
実 LLM(127.0.0.1:8000)は一切叩かない(すべてスクリプト化したモッククライアント)。
"""

from __future__ import annotations

import json
from typing import Any

from app.domains.conversation.events import MemoryEventSink
from app.domains.conversation.prompts import MAIN_AGENT_SYSTEM_PROMPT
from app.domains.conversation.recommend_agent import (
    RECOMMEND_AGENT_MAX_TOKENS,
    build_recommend_agent_messages,
    recommend_agent_guided_schema,
    run_recommend_subagent,
)
from app.domains.conversation.state import ProfileState, TurnState
from app.domains.conversation.types import ToolName, ToolResult
from app.domains.recommendation.types import RecommendationProfile, RecommendFilter

_TAG_VOCABULARY = ["滝", "湧水", "登山", "温泉", "神社"]


class ScriptedClient:
    """`generate()` を呼ぶたびに、スクリプトした応答を 1 つずつ返す。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)


class FailingClient:
    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        raise RuntimeError("生成サーバーが応答しません")


def _act_json(
    *,
    filter: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
    tool: str = "done",
    thought: str = "条件を考える",
) -> str:
    return json.dumps(
        {
            "thought": thought,
            "action": {
                "tool": tool,
                "args": {"filter": filter or {}, "assumptions": assumptions or []},
            },
        },
        ensure_ascii=False,
    )


def _profile(**kwargs: Any) -> RecommendationProfile:
    return RecommendationProfile.model_validate(kwargs)


# ---------------------------------------------------------------------------
# 語彙の局所化(§4: タグ 80 語・mobility はこの SA のプロンプトにだけ載る)
# ---------------------------------------------------------------------------


def test_vocabulary_appears_only_in_recommend_agent_prompt_not_main_agent() -> None:
    messages = build_recommend_agent_messages(
        instruction="滝が見たい", profile=_profile(), tag_vocabulary=_TAG_VOCABULARY
    )
    system_content = messages[0]["content"]

    for tag in _TAG_VOCABULARY:
        assert tag in system_content
    assert "avoid_walk" in system_content
    assert "short_walk_ok" in system_content
    assert "hike_ok" in system_content
    # 移動手段ではなく歩行耐性であることを明記している(23_ux_issues.md §0.3)。
    assert "移動手段" in system_content

    # メインエージェントのシステムプロンプトには、生タグ語彙も mobility の
    # enum 値も一切現れない(§3.3: 語彙の当て外しがメインの手を壊さない)。
    for tag in _TAG_VOCABULARY:
        assert tag not in MAIN_AGENT_SYSTEM_PROMPT
    assert "avoid_walk" not in MAIN_AGENT_SYSTEM_PROMPT
    assert "short_walk_ok" not in MAIN_AGENT_SYSTEM_PROMPT
    assert "hike_ok" not in MAIN_AGENT_SYSTEM_PROMPT


def test_guided_schema_only_allows_done_and_enumerates_vocabulary() -> None:
    schema = recommend_agent_guided_schema(_TAG_VOCABULARY)

    assert schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]
    filter_schema = schema["properties"]["action"]["properties"]["args"]["properties"][
        "filter"
    ]
    assert filter_schema["properties"]["tags"]["items"]["enum"] == _TAG_VOCABULARY
    assert filter_schema["properties"]["mobility"]["anyOf"][1]["enum"] == [
        "avoid_walk",
        "short_walk_ok",
        "hike_ok",
    ]
    # xgrammar 未実装のため uniqueItems は一切使わない。
    assert "uniqueItems" not in json.dumps(schema)


def test_guided_schema_falls_back_to_free_string_when_vocabulary_is_empty() -> None:
    schema = recommend_agent_guided_schema([])
    filter_schema = schema["properties"]["action"]["properties"]["args"]["properties"][
        "filter"
    ]
    assert "enum" not in filter_schema["properties"]["tags"]["items"]


# ---------------------------------------------------------------------------
# instruction → filter 翻訳の呼び出しと guided decoding パラメータ
# ---------------------------------------------------------------------------


async def test_valid_response_is_used_as_is_and_calls_guided_decoding() -> None:
    client = ScriptedClient([_act_json(filter={"tags": ["滝"], "mobility": None})])

    result = await run_recommend_subagent(
        instruction="滝が見たい",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert result.filter.tags == ["滝"]
    assert result.filter.mobility is None
    assert result.dropped == []
    assert result.degraded is False
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["max_tokens"] == RECOMMEND_AGENT_MAX_TOKENS
    response_format = call["extra_body"]["response_format"]
    assert response_format["json_schema"]["strict"] is True


# ---------------------------------------------------------------------------
# C4: 部分不正の要素落とし(23_ux_issues.md §7-2)
# ---------------------------------------------------------------------------


async def test_invalid_tag_is_dropped_but_valid_tag_is_kept() -> None:
    """「滝や湧水などの自然が好きです」の実害シナリオ(§0.3)そのもの。"""

    client = ScriptedClient([_act_json(filter={"tags": ["滝", "山"]})])

    result = await run_recommend_subagent(
        instruction="滝や湧水などの自然が好きです",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert result.filter.tags == ["滝"]
    assert len(result.dropped) == 1
    assert "山" in result.dropped[0]


async def test_invalid_mobility_value_is_dropped_defense_in_depth() -> None:
    """「車で回ります」→ mobility="car" の実害シナリオ(§0.3)の再発防止。

    guided decoding の enum が大半を防ぐが、モックのようにスキーマを経ない
    応答が来ても、コード側の検証(C4)が car のような無効値を落とす。
    """

    client = ScriptedClient([_act_json(filter={"tags": ["滝"], "mobility": "car"})])

    result = await run_recommend_subagent(
        instruction="車で回ります。滝が好きです",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert result.filter.tags == ["滝"]
    assert result.filter.mobility is None
    assert any("car" in value for value in result.dropped)


async def test_all_invalid_elements_fall_back_to_no_filter_not_zero_results() -> None:
    """C4: 全要素が不正でも filter なし(絞り込み無し)で実行できる形になる。"""

    client = ScriptedClient(
        [_act_json(filter={"tags": ["架空タグ"], "mobility": "car", "day": -1})]
    )

    result = await run_recommend_subagent(
        instruction="よくわからないけどおすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert result.filter.tags == []
    assert result.filter.mobility is None
    assert result.filter.day is None
    assert len(result.dropped) == 3


async def test_malformed_type_fields_are_dropped_individually() -> None:
    """guided decoding を経ない応答(モック)で型そのものが壊れていても、

    他の正しい要素は生き残る(要素単位の検証は型不一致にも及ぶ)。
    """

    client = ScriptedClient(
        [
            _act_json(
                filter={
                    "tags": ["滝", 123],
                    "weather_fit": "yes",
                    "area": "",
                    "day": "1",
                }
            )
        ]
    )

    result = await run_recommend_subagent(
        instruction="滝が見たい",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert result.filter.tags == ["滝"]
    assert result.filter.weather_fit is None
    assert result.filter.area is None
    assert result.filter.day is None
    assert len(result.dropped) == 4


# ---------------------------------------------------------------------------
# assumptions(A7: 質問できないときは仮定して推薦し、結果で報告する)
# ---------------------------------------------------------------------------


async def test_assumptions_are_carried_through_and_capped() -> None:
    client = ScriptedClient(
        [
            _act_json(
                filter={},
                assumptions=[
                    "仮定1",
                    "仮定2",
                    "仮定3",
                    "仮定4",
                    "仮定5",
                    "仮定6",
                    "",
                ],
            )
        ]
    )

    result = await run_recommend_subagent(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert result.assumptions == ["仮定1", "仮定2", "仮定3", "仮定4", "仮定5"]


# ---------------------------------------------------------------------------
# 失敗時の縮退(NFR-5: 推薦そのものを止めない)
# ---------------------------------------------------------------------------


async def test_malformed_json_retries_once_then_degrades_to_no_filter() -> None:
    client = ScriptedClient(["not a json", "still not json"])

    result = await run_recommend_subagent(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert len(client.calls) == 2  # 1回だけ再試行する
    assert result.degraded is True
    assert result.filter.tags == []
    assert result.assumptions  # 縮退した事実を仮定として報告する


async def test_unexpected_tool_value_is_treated_as_contract_violation() -> None:
    """段3の SA は done だけ持つ。想定外の tool 値は契約違反として再試行する。"""

    client = ScriptedClient(
        [_act_json(tool="ask_user"), _act_json(filter={"tags": ["滝"]})]
    )

    result = await run_recommend_subagent(
        instruction="滝が見たい",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert len(client.calls) == 2
    assert result.degraded is False
    assert result.filter.tags == ["滝"]


async def test_generation_failure_degrades_without_raising() -> None:
    result = await run_recommend_subagent(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=FailingClient(),
    )

    assert result.degraded is True
    assert result.filter == RecommendFilter()


# ---------------------------------------------------------------------------
# state:step progress(§11: SA の判定 LLM 実行中の実況)
# ---------------------------------------------------------------------------


async def test_progress_step_event_is_emitted_before_the_llm_call() -> None:
    client = ScriptedClient([_act_json(filter={"tags": ["滝"]})])
    sink = MemoryEventSink()

    await run_recommend_subagent(
        instruction="滝が見たい",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
        event_sink=sink,
    )

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event == "state"
    assert event.data["kind"] == "step"
    assert event.data["tool"] == "recommend"
    assert event.data["status"] == "progress"


# ---------------------------------------------------------------------------
# ask_user(§4 (b)): プロフィールが薄いときの 1 回だけの質問
# ---------------------------------------------------------------------------


class FakeAskTools:
    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    async def ask_user(self, *, step_id: int, args: Any) -> Any:
        self.calls.append({"step_id": step_id, "args": args})
        return self.queue.pop(0)


def _state() -> TurnState:
    return TurnState(
        turn_id="turn", thread_id=1, user_id=1, utterance="おすすめは？", profile=ProfileState()
    )


def _ask_json(
    *, slot: str = "party", reason: str = "どなたと行かれますか", tool: str = "ask_user"
) -> str:
    return json.dumps(
        {
            "thought": "情報が薄いので聞く",
            "action": {
                "tool": tool,
                "args": {
                    "slot": slot,
                    "reason": reason,
                    "options": [
                        {"label": "家族", "value": "family_kids"},
                        {"label": "一人", "value": "solo"},
                    ],
                },
            },
        },
        ensure_ascii=False,
    )


def _update_profile_json(*, party: str | None = None) -> str:
    delta = None
    if party is not None:
        delta = {
            "interests": {},
            "party": party,
            "mobility": None,
            "pace": None,
            "avoid": [],
            "notes": None,
        }
    return json.dumps({"profile_delta": delta, "score_adjustments": []}, ensure_ascii=False)


def _ask_result(*, answer: str = "家族です", answered_by: str = "chip") -> ToolResult:
    return ToolResult(
        step_id=1, tool=ToolName.ASK_USER, data={"answer": answer, "answered_by": answered_by}
    )


async def test_ask_user_branch_is_offered_only_when_state_and_tools_are_given() -> None:
    """既存の呼び出し元(state/tools 無し)は従来どおり done 単発判定のまま。"""

    client = ScriptedClient([_act_json(filter={"tags": ["滝"]})])

    result = await run_recommend_subagent(
        instruction="滝が見たい",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
    )

    assert result.filter.tags == ["滝"]
    schema = json.loads(json.dumps(recommend_agent_guided_schema(_TAG_VOCABULARY)))
    assert schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]


async def test_ask_user_then_update_profile_then_redecision_then_done() -> None:
    """§4 (b): 質問 → 回答 → update_profile 再実行 → 更新後プロフィールで再判定 → done。"""

    state = _state()
    tools = FakeAskTools()
    tools.queue = [_ask_result(answer="家族です")]
    client = ScriptedClient(
        [
            _ask_json(slot="party", reason="どなたと行かれますか"),
            _update_profile_json(party="family_kids"),
            _act_json(filter={"tags": ["滝"]}),
        ]
    )

    result = await run_recommend_subagent(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
        state=state,
        tools=tools,
        step_id=7,
    )

    assert result.filter.tags == ["滝"]
    assert result.degraded is False
    assert len(tools.calls) == 1
    assert tools.calls[0]["step_id"] == 7
    assert state.profile.party == "family_kids"
    assert state.ask_user_count == 1

    # 1 回目の呼び出しだけ ask_user 分岐を含む anyOf、以降は done のみ。
    first_schema = client.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert "anyOf" in first_schema["properties"]["action"]
    third_schema = client.calls[2]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert third_schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]

    # 2026-08-04 レビュー是正(High・裁定3a): 再判定プロンプト(3回目の
    # 呼び出し)には質問文 + 回答の両方が含まれる。プロフィール更新だけに
    # 頼ると `ProfileDelta` に無いフィールド(dates/origin 等)の回答が
    # 完全に失われうるため。
    redecision_prompt = client.calls[2]["messages"][1]["content"]
    assert "どなたと行かれますか" in redecision_prompt
    assert "家族です" in redecision_prompt


async def test_second_ask_user_attempt_degrades_to_done_without_asking_again() -> None:
    """A7: 質問は 1 回まで。ガードで落ちた/再質問された場合も done へ縮退する。"""

    state = _state()
    state.asked_slots = ["party"]  # A1: 既に聞いた slot への再質問は必ず落ちる。
    tools = FakeAskTools()
    client = ScriptedClient(
        [
            _ask_json(slot="party", reason="どなたと行かれますか"),
            _act_json(filter={"tags": ["温泉"]}),
        ]
    )

    result = await run_recommend_subagent(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
        state=state,
        tools=tools,
        step_id=1,
    )

    assert result.filter.tags == ["温泉"]
    assert tools.calls == []  # ガードで落ちたので実際には呼ばれない
    assert state.ask_user_count == 0


async def test_ask_user_branch_excluded_when_r4_budget_already_exhausted() -> None:
    """裁定16(2026-08-04レビュー是正): R4(メイン・SA合算で1ターン2回まで)に

    既に到達していれば、SA の guided schema からも `ask_user` を外す。
    実行時ガード(R4)だけだと、SA が無効な質問を選んで1周を浪費できる。
    """

    state = _state()
    state.ask_user_count = 2  # 既にメイン側で 2 回使い切っている想定。
    tools = FakeAskTools()
    client = ScriptedClient([_act_json(filter={"tags": ["温泉"]})])

    result = await run_recommend_subagent(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        client=client,
        state=state,
        tools=tools,
        step_id=1,
    )

    assert result.filter.tags == ["温泉"]
    assert tools.calls == []
    schema = client.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]
