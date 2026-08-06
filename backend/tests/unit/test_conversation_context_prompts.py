"""① load_context の基本挙動と、メインループ/respond プロンプトの

順序・guided schema の環境制約を検査する(段2)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.domains.conversation.context import load_context
from app.domains.conversation.prompts import (
    MAIN_AGENT_SYSTEM_PROMPT,
    build_main_agent_messages,
    build_respond_messages,
    main_agent_done_only_schema,
    main_agent_guided_schema,
)
from app.domains.conversation.state import (
    ContextSnapshot,
    DegradedState,
    ItineraryState,
    MessageState,
    ProfileState,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import AskUserArgs, ResponseMode, TrajectoryStep, slot_values
from app.domains.itinerary.types import Itinerary


def _message(seq: int, role: str, content: str) -> MessageState:
    return MessageState(
        id=seq,
        seq=seq,
        role=role,
        content=content,
        status="complete",
        meta={},
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )


def _spot() -> SpotFact:
    return SpotFact(
        spot_id="spot_001",
        name_ja="鶴間池",
        kind="poi",
        aliases_ja=["つるまいけ"],
        tags_ja=["自然"],
    )


def _state(history: str = "") -> TurnState:
    spot = _spot()
    return TurnState(
        turn_id="turn",
        thread_id=1,
        user_id=1,
        utterance="その理由を詳しく",
        profile=ProfileState(),
        history=history,
        spot_id_vocab=[spot.spot_id],
        spot_names={spot.spot_id: spot.name_ja},
        spot_catalog={spot.spot_id: spot},
    )


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


class _SnapshotRepository:
    def __init__(self, snapshot: ContextSnapshot) -> None:
        self.snapshot = snapshot

    async def load_snapshot(self, user_id: int) -> ContextSnapshot:
        assert user_id == 1
        return self.snapshot


async def test_load_context_builds_spot_vocabulary_no_default_origin_without_itinerary() -> None:
    """既存旅程が無ければ `default_origin_spot_id` は `None`(2026-08-04、

    [25 §1-4] の是正)。旧実装は facility 種別のソート順先頭・
    `min(snapshot.spots)` へフォールバックしており、未確認の起点基準で
    所要時間・旅程が黙って作られていた。
    """

    spot = _spot()
    snapshot = ContextSnapshot(
        thread_id=1,
        profile=ProfileState(),
        itinerary=None,
        messages=[_message(1, "user", "鶴間池について教えて")],
        last_candidates=[],
        presented_spot_ids=[],
        asked_slots=[],
        pending_ask=None,
        resolved_ambiguities=[],
        pending_constraints=[],
        realtime={},
        spots={spot.spot_id: spot},
        tag_vocabulary=["自然", "滝"],
    )

    state = await load_context(
        _SnapshotRepository(snapshot),
        user_id=1,
        utterance="鶴間池は静かですか",
    )

    assert state.spot_id_vocab == ["spot_001"]
    assert state.default_origin_spot_id is None
    assert state.tag_vocabulary == ["自然", "滝"]
    # 段2で `ask_user` の中断・復帰路は廃止したため、tool_results 相当の
    # 復帰処理は存在しない(TurnState に該当フィールドも無い)。
    assert not hasattr(state, "tool_results")


async def test_load_context_default_origin_uses_existing_itinerary_day_one_origin() -> None:
    """既存旅程があれば、その 1 日目の origin を既定起点として使う(維持する挙動)。"""

    spot = _spot()
    itinerary = Itinerary.model_validate(
        {
            "days": [
                {
                    "date": "2026-08-10",
                    "start_min": 540,
                    "end_min": 1020,
                    "origin": {"kind": "spot", "spot_id": "spot_001"},
                    "destination": {"kind": "spot", "spot_id": "spot_001"},
                    "items": [],
                }
            ],
            "version": 1,
        }
    )
    snapshot = ContextSnapshot(
        thread_id=1,
        profile=ProfileState(),
        itinerary=ItineraryState(itinerary=itinerary, constraints=[], parent_version=None),
        messages=[],
        last_candidates=[],
        presented_spot_ids=[],
        asked_slots=[],
        pending_ask=None,
        resolved_ambiguities=[],
        pending_constraints=[],
        realtime={},
        spots={spot.spot_id: spot},
        tag_vocabulary=[],
    )

    state = await load_context(
        _SnapshotRepository(snapshot),
        user_id=1,
        utterance="続きを教えて",
    )

    assert state.default_origin_spot_id == "spot_001"


def test_main_agent_context_order_and_utterance_is_last() -> None:
    state = _state("u: 前の発話\na: 前の応答")
    now = datetime(2026, 8, 1, 16, 0, tzinfo=UTC)

    messages = build_main_agent_messages(state, reduced=False, now=now)
    dynamic = messages[1]["content"]

    assert "今日は 2026-08-02(日)です。『明日』は 2026-08-03 を指します。" in dynamic
    assert "④ 会話履歴" in dynamic
    assert state.history in dynamic
    assert dynamic.endswith(state.utterance)
    assert (
        dynamic.index("②")
        < dynamic.index("③")
        < dynamic.index("④")
        < dynamic.index("⑤")
        < dynamic.index("⑥")
    )
    assert messages[0]["content"] == MAIN_AGENT_SYSTEM_PROMPT
    assert "今日は 2026-08-02" not in MAIN_AGENT_SYSTEM_PROMPT
    # spot_id はメインループのプロンプトに一切出さない。
    assert "spot_001" not in dynamic


def test_main_agent_context_is_byte_identical_across_turns_for_same_state() -> None:
    """①(system)はターン間で byte 同一(prefix caching)。"""

    state = _state()
    first = build_main_agent_messages(state, reduced=False)
    second = build_main_agent_messages(state, reduced=False)

    assert first[0]["content"] == second[0]["content"] == MAIN_AGENT_SYSTEM_PROMPT


def test_main_agent_context_includes_trajectory_and_reduced_note() -> None:
    state = _state()
    state.trajectory = [
        TrajectoryStep(
            tool="recommend",
            thought="まず探す",
            args={"instruction": "滝が見たい"},
            observation="おすすめ:\n  1. 鶴間池",
        )
    ]

    normal = build_main_agent_messages(state, reduced=False)[1]["content"]
    reduced = build_main_agent_messages(state, reduced=True)[1]["content"]

    assert "[手1] tool=recommend" in normal
    assert "おすすめ:" in normal
    assert "まとめに入って" in reduced
    assert "まとめに入って" not in normal


def test_main_agent_guided_schema_has_exclusive_tool_enum_per_branch() -> None:
    schema = main_agent_guided_schema(["c_001", "c_002"])

    assert list(schema["properties"]) == ["thought", "action"]
    assert not _contains_key(schema, "uniqueItems")

    branches = schema["properties"]["action"]["anyOf"]
    tool_values = [branch["properties"]["tool"]["enum"] for branch in branches]
    assert tool_values == [
        ["recommend"],
        ["plan_itinerary"],
        ["edit_itinerary"],
        ["search_knowledge"],
        ["ask_user"],
        ["done"],
    ]
    for branch in branches:
        assert len(branch["properties"]["tool"]["enum"]) == 1
        assert branch["additionalProperties"] is False

    done_branch = branches[-1]
    assert done_branch["properties"]["args"]["properties"] == {}

    remove_schema = branches[1]["properties"]["args"]["properties"]["constraints"]
    remove_item_enum = remove_schema["anyOf"][1]["properties"]["remove"]["items"]
    assert remove_item_enum["enum"] == ["c_001", "c_002"]

    empty_vocab = main_agent_guided_schema([])
    empty_branch = empty_vocab["properties"]["action"]["anyOf"][1]
    empty_remove = empty_branch["properties"]["args"]["properties"]["constraints"]
    assert empty_remove["anyOf"][1]["properties"]["remove"]["maxItems"] == 0


def test_main_agent_guided_schema_plan_itinerary_branch_has_candidate_spots() -> None:
    """ADR-0022: plan_itinerary 分岐に candidate_spots(string 配列)がある。

    string 配列なので xgrammar の「配列要素内の number」既知不具合
    ([25 §2-1])には該当しない。
    """

    schema = main_agent_guided_schema(["c_001"])
    branches = schema["properties"]["action"]["anyOf"]
    plan_branch = next(
        branch for branch in branches if branch["properties"]["tool"]["enum"] == ["plan_itinerary"]
    )
    properties = plan_branch["properties"]["args"]["properties"]

    assert "candidate_spots" in properties
    assert properties["candidate_spots"]["type"] == "array"
    assert properties["candidate_spots"]["items"] == {"type": "string", "minLength": 1}
    assert "candidate_spots" in plan_branch["properties"]["args"]["required"]


def test_main_agent_guided_schema_ask_user_branch_is_exclusive_by_kind() -> None:
    """欠陥1(25 §1-6): `ask_user` 分岐は `kind` ごとの anyOf で

    `slot`/`surface` の排他をスキーマ側で強制する(平坦スキーマだと
    `kind="clarify"` に `slot` 非 null を LLM が書けてしまい、
    `AskUserArgs.model_validate` の ValidationError がターンを落としていた)。
    """

    schema = main_agent_guided_schema(["c_001"])
    branches = schema["properties"]["action"]["anyOf"]
    ask_user_branch = next(
        branch for branch in branches if branch["properties"]["tool"]["enum"] == ["ask_user"]
    )
    args_branches = ask_user_branch["properties"]["args"]["anyOf"]
    assert len(args_branches) == 2

    preference_branch = next(
        branch
        for branch in args_branches
        if branch["properties"]["kind"]["enum"] == ["preference"]
    )
    clarify_branch = next(
        branch for branch in args_branches if branch["properties"]["kind"]["enum"] == ["clarify"]
    )

    # preference: slot は enum(null 不可)、surface は null 固定。
    assert preference_branch["properties"]["slot"]["type"] == "string"
    assert "enum" in preference_branch["properties"]["slot"]
    assert preference_branch["properties"]["surface"] == {"type": "null"}
    assert preference_branch["additionalProperties"] is False

    # clarify: slot は null 固定、surface は非空 string。
    assert clarify_branch["properties"]["slot"] == {"type": "null"}
    assert clarify_branch["properties"]["surface"]["type"] == "string"
    assert clarify_branch["properties"]["surface"]["minLength"] == 1
    assert clarify_branch["additionalProperties"] is False

    for branch in args_branches:
        assert set(branch["required"]) == {"kind", "slot", "surface", "reason", "options"}

    # L-5(2026-08-04、レビュー是正): "reason"/"options" は分岐間で共有オブジェ
    # クトにしない(片方を書き換えるともう片方まで変わる不変条件違反を防ぐ)。
    assert preference_branch["properties"]["options"] is not clarify_branch["properties"]["options"]
    assert preference_branch["properties"]["reason"] is not clarify_branch["properties"]["reason"]


def test_main_agent_guided_schema_ask_user_branches_match_ask_user_args_pydantic() -> None:
    """L-4: スキーマの各分岐の最小インスタンスが `AskUserArgs` の検証を通り、

    分岐を交差させた組み合わせ(clarify なのに slot 非 null)は
    `ValidationError` になることを確認する(スキーマ⇔pydantic の整合)。
    """

    preference_instance = {
        "kind": "preference",
        "slot": slot_values()[0],
        "surface": None,
        "reason": "確認させてください",
        "options": [
            {"label": "はい", "value": "yes"},
            {"label": "いいえ", "value": "no"},
        ],
    }
    clarify_instance = {
        "kind": "clarify",
        "slot": None,
        "surface": "2番目のやつ",
        "reason": "候補が 2 つあります",
        "options": [
            {"label": "鶴間池", "value": "spot_001"},
            {"label": "元滝伏流水", "value": "spot_002"},
        ],
    }

    AskUserArgs.model_validate(preference_instance)
    AskUserArgs.model_validate(clarify_instance)

    crossed = {**clarify_instance, "slot": slot_values()[0]}
    with pytest.raises(ValidationError):
        AskUserArgs.model_validate(crossed)


def test_main_agent_done_only_schema_only_allows_done() -> None:
    schema = main_agent_done_only_schema()

    assert schema["properties"]["action"]["properties"]["tool"]["enum"] == ["done"]
    assert not _contains_key(schema, "uniqueItems")


def test_respond_context_includes_trajectory_and_degradation() -> None:
    state = _state()
    state.trajectory = [
        TrajectoryStep(
            tool="search_knowledge",
            thought="調べる",
            args={"request": "由来"},
            observation="検索結果(coverage=full): ...",
        )
    ]
    state.degraded = [
        DegradedState(
            code="rerank_degraded",
            stage="recommend",
            message="スコア順で確定しました",
        )
    ]

    dynamic = build_respond_messages(state, mode=ResponseMode.EXPLANATION)[1]["content"]

    assert "mode: explanation" in dynamic
    assert "[手1] tool=search_knowledge" in dynamic
    assert '"code":"rerank_degraded"' in dynamic
    # dialogue_style.md §4: ④ ユーザーの発話の後に ⑤ 素材が続く(末尾は
    # ⑤ 素材節になる。素材を渡さない呼び出しでは「(なし)」になる)。
    assert "④ ユーザーの発話:\n" + state.utterance in dynamic
    assert dynamic.endswith("⑤ 素材(このターンで提示したスポットの説明):\n(なし)")
