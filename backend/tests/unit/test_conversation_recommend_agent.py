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


def test_ask_user_slot_enum_is_limited_to_the_four_preference_slots() -> None:
    """L-2(2026-08-06 レビュー是正、ADR-0024): SA が聞けるスロットは

    party/mobility/pace/interests の 4 種だけ。dates/origin/onboarding は
    メインエージェントの担当なので SA には聞かせない(§4 改訂)。
    """

    schema = recommend_agent_guided_schema(_TAG_VOCABULARY, allow_ask_user=True)
    ask_user_branch = next(
        branch
        for branch in schema["properties"]["action"]["anyOf"]
        if branch["properties"]["tool"]["enum"] == ["ask_user"]
    )
    slot_enum = ask_user_branch["properties"]["args"]["properties"]["slot"]["enum"]
    assert sorted(slot_enum) == sorted(["party", "mobility", "pace", "interests"])


def test_ask_user_prompt_slot_vocabulary_is_limited_to_the_four_preference_slots() -> None:
    """L-2: プロンプトに載る slot 語彙も同じ 4 種に絞る。"""

    messages = build_recommend_agent_messages(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        allow_ask_user=True,
    )
    system_content = messages[0]["content"]
    for slot in ("party", "mobility", "pace", "interests"):
        assert slot in system_content
    for slot in ("dates", "origin", "onboarding"):
        assert slot not in system_content


def test_ask_user_prompt_is_directive_not_permissive() -> None:
    """実機是正(2026-08-06、ADR-0024): 「聞けます」という許可形のプロンプトでは

    LLM(gemma-4-31B)が 1 問(同行者)だけで done を選んで切り上げた
    (コールドスタートでも同行者しか聞かなかった)。指示形に強め、
    interests → party → mobility の優先順を明記する(§4 改訂)。pace は
    schema の enum には残すが、優先列挙(旅程向けで推薦単独の質を左右しない)
    には含めない。
    """

    messages = build_recommend_agent_messages(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        allow_ask_user=True,
    )
    system_content = messages[0]["content"]

    # 指示形(「聞いてください」)。旧「聞けます」「不明なスロットが複数
    # あれば周を分けて順に聞けます」という許可形の文言は残っていない。
    assert "done を選ばず ask_user で聞いてください" in system_content
    assert "聞く順は interests → party → mobility" in system_content
    assert "聞けます" not in system_content

    # done を選んでよい条件(3 スロットが埋まった/お任せ/聞けない事情が
    # 観測に返っている)を境界に明記する。
    assert "done を選んでよいのは" in system_content
    assert "お任せ" in system_content
    assert "タイムアウト・聞けなかった質問" in system_content

    # pace は schema の args 語彙(下記 test_ask_user_prompt_slot_vocabulary_
    # is_limited_to_the_four_preference_slots で確認済み)には残るが、
    # 優先列挙の文には現れない(旅程向けのスロットで、推薦単独の質を左右
    # しないため)。
    priority_sentence = "聞く順は interests → party → mobility です"
    assert priority_sentence in system_content
    assert "pace" not in priority_sentence


def test_ask_user_prompt_boundary_is_permissive_when_ask_user_is_disabled() -> None:
    """R4 到達等で `allow_ask_user=False` のときは、従来どおり聞けない旨を

    伝え、指示形の質問義務(done を選んでよい条件)は現れない。
    """

    messages = build_recommend_agent_messages(
        instruction="おすすめを教えて",
        profile=_profile(),
        tag_vocabulary=_TAG_VOCABULARY,
        allow_ask_user=False,
    )
    system_content = messages[0]["content"]

    assert "質問はできません。" in system_content
    assert "done を選んでよいのは" not in system_content
    assert "ask_user: 興味" not in system_content


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
# ask_user(§4 (b)): プロフィールが薄いときの質問(2026-08-06、ADR-0024:
# 旧 A7 の「1 回まで」制限は廃止。R4=6 の枠内で必要なだけ質問できる)。
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


def _update_profile_json(
    *, party: str | None = None, mobility: str | None = None
) -> str:
    delta = None
    if party is not None or mobility is not None:
        delta = {
            "interests": {},
            "party": party,
            "mobility": mobility,
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

    # 2026-08-06(ADR-0024): 旧 A7 の「1 回まで」は廃止したため、質問した
    # 後(3 回目の呼び出し)も R4(=6)の枠内なら ask_user 分岐は schema から
    # 外れない(anyOf のまま)。今回は LLM 自身が done を選んでいる。
    first_schema = client.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert "anyOf" in first_schema["properties"]["action"]
    third_schema = client.calls[2]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert "anyOf" in third_schema["properties"]["action"]

    # 2026-08-04 レビュー是正(High・裁定3a): 再判定プロンプト(3回目の
    # 呼び出し)には質問文 + 回答の両方が含まれる。プロフィール更新だけに
    # 頼ると `ProfileDelta` に無いフィールド(dates/origin 等)の回答が
    # 完全に失われうるため。
    redecision_prompt = client.calls[2]["messages"][1]["content"]
    assert "どなたと行かれますか" in redecision_prompt
    assert "家族です" in redecision_prompt


async def test_ask_user_can_ask_two_slots_across_two_rounds() -> None:
    """2026-08-06(ADR-0024): 旧 A7 の 1 回制限を廃止。

    R4(=6)の枠内であれば、周を分けて複数スロットを聞ける
    (§4 改訂: 「不明なスロットが複数あれば周を分けて順に聞けます」)。
    質問のたびに回答が act に返り、update_profile 再実行を経て、
    最終的に done へ至る。
    """

    state = _state()
    tools = FakeAskTools()
    tools.queue = [
        _ask_result(answer="家族です"),
        _ask_result(answer="30分程度なら"),
    ]
    mobility_update_json = json.dumps(
        {
            "profile_delta": {
                "interests": {},
                "party": None,
                "mobility": "short_walk_ok",
                "pace": None,
                "avoid": [],
                "notes": None,
            },
            "score_adjustments": [],
        },
        ensure_ascii=False,
    )
    client = ScriptedClient(
        [
            _ask_json(slot="party", reason="どなたと行かれますか"),
            _update_profile_json(party="family_kids"),
            _ask_json(slot="mobility", reason="どのくらい歩けますか"),
            mobility_update_json,
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
    assert result.degraded is False
    assert len(tools.calls) == 2
    assert state.ask_user_count == 2
    assert state.profile.party == "family_kids"
    assert state.profile.mobility == "short_walk_ok"
    assert len(client.calls) == 5


async def test_ask_user_asks_interests_then_party_before_done_on_cold_start() -> None:
    """実機是正(2026-08-06、ADR-0024): コールドスタート(プロフィール空)で

    指示形プロンプトのもと、interests → party の優先順どおり 2 問聞いてから
    done に至る経路が通ることを確認する(許可形 → 指示形への文言変更が、
    複数スロットを周を分けて聞くという既存の仕組みを壊していないことの
    回帰確認)。
    """

    state = _state()
    tools = FakeAskTools()
    tools.queue = [
        _ask_result(answer="自然が好きです"),
        _ask_result(answer="家族です"),
    ]
    interests_update_json = json.dumps(
        {
            "profile_delta": {
                "interests": {"nature": 0.6},
                "party": None,
                "mobility": None,
                "pace": None,
                "avoid": [],
                "notes": None,
            },
            "score_adjustments": [],
        },
        ensure_ascii=False,
    )
    client = ScriptedClient(
        [
            _ask_json(slot="interests", reason="どんなことに興味がありますか"),
            interests_update_json,
            _ask_json(slot="party", reason="どなたと行かれますか"),
            _update_profile_json(party="family_kids"),
            _act_json(filter={"tags": ["湧水"]}),
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

    assert result.filter.tags == ["湧水"]
    assert result.degraded is False
    assert len(tools.calls) == 2
    assert state.ask_user_count == 2
    assert state.profile.interests == {"nature": 0.6}
    assert state.profile.party == "family_kids"
    assert len(client.calls) == 5


async def test_ask_user_rejected_slot_can_retry_with_a_different_slot() -> None:
    """M-1(2026-08-06 レビュー是正、ADR-0024): 1 回の拒否では質問能力を

    止めない。拒否理由が次周のプロンプトに「聞けなかった質問」として現れ、
    SA は別のスロットを聞き直せる(1 回の拒否で全面停止だった旧実装からの
    是正)。
    """

    state = _state()
    state.asked_slots = ["party"]  # A1: 既に聞いた slot への再質問は必ず落ちる。
    tools = FakeAskTools()
    tools.queue = [_ask_result(answer="30分程度なら")]
    client = ScriptedClient(
        [
            _ask_json(slot="party", reason="どなたと行かれますか"),
            _ask_json(slot="mobility", reason="どのくらい歩けますか"),
            _update_profile_json(mobility="short_walk_ok"),
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
    assert result.degraded is False
    # party の質問はガードで落ちて実行されず、mobility だけが実行される。
    assert len(tools.calls) == 1
    assert state.ask_user_count == 1
    assert state.profile.mobility == "short_walk_ok"

    # 2 周目のプロンプトに「聞けなかった質問」(party の拒否理由)が現れる。
    second_prompt = client.calls[1]["messages"][1]["content"]
    assert "聞けなかった質問" in second_prompt
    assert "slot=party" in second_prompt


async def test_ask_user_same_slot_rejected_twice_stops_asking_and_converges() -> None:
    """M-1(2026-08-06 レビュー是正、ADR-0024): 同じスロットが 2 度弾かれたら

    そのスロットは解消できないと判断して打ち切る(無限ループ防止)。
    """

    state = _state()
    state.asked_slots = ["party"]  # A1: 既に聞いた slot への再質問は必ず落ちる。
    tools = FakeAskTools()
    client = ScriptedClient(
        [
            _ask_json(slot="party", reason="どなたと行かれますか"),
            _ask_json(slot="party", reason="念のためもう一度"),
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
    # 拒否 → 拒否(同じ slot) → done の 3 周だけで終わる(無限ループしない)。
    assert len(client.calls) == 3
    third_schema = client.calls[2]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert third_schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]


async def test_ask_user_timeout_disables_further_asking_and_converges() -> None:
    """H-1(2026-08-06 レビュー是正、ADR-0024): タイムアウトが起きたら

    `state.ask_timed_out` が立ち、次周は ask_user がスキーマから外れて
    done へ収束する。旧実装はタイムアウトを観測できず(executed=True・
    answer_text=None でプロンプトが不変のまま同じ質問を選び続けられ)、
    最大 R4 回 × 10 分ブロックしうる不具合があった。
    """

    state = _state()
    tools = FakeAskTools()
    tools.queue = [
        ToolResult(
            step_id=1,
            tool=ToolName.ASK_USER,
            data={
                "answer": "(タイムアウトのため回答がありませんでした)",
                "answered_by": "timeout",
            },
        )
    ]
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
    assert state.ask_timed_out is True
    assert len(tools.calls) == 1
    assert len(client.calls) == 2
    second_schema = client.calls[1]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert second_schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]


async def test_ask_user_parse_failure_disables_asking_and_converges() -> None:
    """`_parse_recommend_ask_args` が None を返す経路(契約違反の args)。

    質問を確定できなかった場合は M-1(同じ slot 2 回)の対象外として即座に
    打ち切り、次周は done へ収束する(壊れた出力を選び続けさせない)。
    """

    state = _state()
    tools = FakeAskTools()
    malformed_ask_json = json.dumps(
        {
            "thought": "情報が薄いので聞く",
            "action": {
                "tool": "ask_user",
                # options が list でないため `_parse_recommend_ask_args` は
                # None を返す(guided decoding では通常起きないが、モックや
                # 逸脱応答に備えた防御をここで検査する)。
                "args": {
                    "slot": "party",
                    "reason": "どなたと行かれますか",
                    "options": "not-a-list",
                },
            },
        },
        ensure_ascii=False,
    )
    client = ScriptedClient([malformed_ask_json, _act_json(filter={"tags": ["温泉"]})])

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
    assert state.ask_user_count == 0
    second_schema = client.calls[1]["extra_body"]["response_format"]["json_schema"]["schema"]
    assert second_schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]


async def test_ask_user_branch_excluded_when_r4_budget_already_exhausted() -> None:
    """裁定16(2026-08-04レビュー是正・2026-08-06 R4=6 に改訂、ADR-0024): R4

    (メイン・SA合算で1ターン6回まで)に既に到達していれば、SA の guided
    schema からも `ask_user` を外す。実行時ガード(R4)だけだと、SA が
    無効な質問を選んで1周を浪費できる。
    """

    state = _state()
    state.ask_user_count = 6  # 既にメイン側で 6 回使い切っている想定。
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
