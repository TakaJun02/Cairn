"""① load_context の基本挙動と、メインループ/respond プロンプトの

順序・guided schema の環境制約を検査する(段2)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
    MessageState,
    ProfileState,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import ResponseMode, TrajectoryStep


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


async def test_load_context_builds_spot_vocabulary_and_default_origin() -> None:
    spot = _spot()
    snapshot = ContextSnapshot(
        thread_id=1,
        profile=ProfileState(),
        itinerary=None,
        messages=[_message(1, "user", "鶴間池について教えて")],
        last_candidates=[],
        presented_spot_ids=[],
        asked_slots=[],
        ask_streak=0,
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
    assert state.default_origin_spot_id == "spot_001"
    assert state.tag_vocabulary == ["自然", "滝"]
    # 段2で `ask_user` の中断・復帰路は廃止したため、tool_results 相当の
    # 復帰処理は存在しない(TurnState に該当フィールドも無い)。
    assert not hasattr(state, "tool_results")


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
    assert dynamic.endswith(state.utterance)
