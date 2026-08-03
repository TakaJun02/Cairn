"""履歴 3 層、prompt 順序、guided schema の環境制約を検査する。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domains.conversation.context import load_context
from app.domains.conversation.executor import (
    _merged_constraints,
    build_recommendation_context,
)
from app.domains.conversation.prompts import (
    UNDERSTAND_SYSTEM_PROMPT,
    build_respond_messages,
    build_understand_messages,
    understand_guided_schema,
)
from app.domains.conversation.state import (
    ContextSnapshot,
    DegradedState,
    MessageState,
    ProfileState,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import (
    ConstraintDraft,
    ProfileDelta,
    ResponseMode,
    ScoreAdjustment,
    Slot,
)
from app.domains.recommendation.types import Mobility
from app.seeds import load_seed_bundle


def _message(
    seq: int,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
) -> MessageState:
    return MessageState(
        id=seq,
        seq=seq,
        role=role,
        content=content,
        status="complete",
        meta=meta or {},
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )


def _state(history: str = "") -> TurnState:
    spot = SpotFact(
        spot_id="spot_001",
        name_ja="鶴間池",
        kind="poi",
        aliases_ja=["つるまいけ"],
        tags_ja=["自然"],
    )
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


def test_understand_and_respond_receive_the_same_history_and_utterance_is_last() -> None:
    state = _state("u: 前の発話\na: 前の応答")
    # UTC では前日でも、基準日は JST の 2026-08-02 になる。
    now = datetime(2026, 8, 1, 16, 0, tzinfo=UTC)

    understand_messages = build_understand_messages(state, now=now)
    respond_messages = build_respond_messages(
        state,
        mode=ResponseMode.EXPLANATION,
        now=now,
    )

    for messages in (understand_messages, respond_messages):
        dynamic = messages[1]["content"]
        assert (
            "今日は 2026-08-02(日)です。"
            "『明日』は 2026-08-03 を指します。"
        ) in dynamic
        assert "⑤ 会話履歴" in dynamic
        assert state.history in dynamic
        assert dynamic.endswith(state.utterance)
        assert (
            dynamic.index("③ ")
            < dynamic.index("④ ")
            < dynamic.index("⑤ ")
            < dynamic.index("⑥ ")
        )
    assert understand_messages[0]["content"] == UNDERSTAND_SYSTEM_PROMPT
    assert "今日は 2026-08-02" not in UNDERSTAND_SYSTEM_PROMPT


def test_understand_prompt_documents_itinerary_endpoint_and_edit_target_shapes() -> None:
    assert 'origin={"kind":"facility","id":"spot_011"}' in UNDERSTAND_SYSTEM_PROMPT
    assert '{op:"remove", targets:[spot_id]}' in UNDERSTAND_SYSTEM_PROMPT
    assert 'targets:[spot_id] または "$N.spot_ids"' in UNDERSTAND_SYSTEM_PROMPT


def test_understand_prompt_contains_all_raw_tags_and_validated_tool_values() -> None:
    raw_tags = [row["tag"] for row in load_seed_bundle().tag_vocabulary]
    assert len(raw_tags) == 80
    state = _state()
    state.tag_vocabulary = raw_tags

    system, dynamic = (
        message["content"] for message in build_understand_messages(state)
    )

    assert "生タグ語彙（80語・ここにある語だけ使用可）" in dynamic
    assert " | ".join(raw_tags) in dynamic
    assert all(tag in dynamic for tag in raw_tags)
    assert " | ".join(raw_tags) not in system
    assert "語彙に無い概念は tags に入れず" in system
    assert "「山」は語彙に無い" in system
    assert "「登山」か「鳥海山」" in system

    for mobility in Mobility:
        assert f'"{mobility.value}"' in system
    assert "mobility は移動手段ではなく歩行耐性" in system
    assert "「車で行く」「車で回る」だけでは歩行耐性は不明" in system
    assert "recommend.filter.mobility に書きません" in system
    assert "weather_fit?:true|false" in system
    assert "weather_fit は boolean" in system
    assert "area?:非空文字列" in system
    assert "day?:1以上の整数" in system
    assert "exclude?:[spot_id]" in system

    for slot in Slot:
        assert f'"{slot.value}"' in system
    for interpretation in (
        "all_matches",
        "single_match",
        "current_itinerary",
        "last_candidates",
    ):
        assert interpretation in system
    assert "kind=preference は slot が必須で surface は不可" in system
    assert "kind=clarify は surface が" in system
    assert "search_knowledge: {request:非空文字列, spot_id?:spot_id}" in system

    for op in (
        "add",
        "remove",
        "move",
        "replace",
        "lock",
        "set_stay",
        "set_time",
        "revert",
    ):
        assert f'op:"{op}"' in system
    assert "各 op では ? の無い引数が必須" in system
    assert "set_time は arrive/depart の少なくとも" in system
    assert "min:1以上整数" in system
    assert "to_version?:1以上整数" in system

    respond_dynamic = build_respond_messages(
        state,
        mode=ResponseMode.EXPLANATION,
    )[1]["content"]
    assert "生タグ語彙（80語" not in respond_dynamic


def test_respond_context_keeps_degradation_and_removed_constraints() -> None:
    state = _state()
    state.constraints_remove = ["c_003"]
    state.degraded = [
        DegradedState(
            code="rerank_degraded",
            stage="act",
            message="スコア順で確定しました",
        )
    ]

    dynamic = build_respond_messages(
        state,
        mode=ResponseMode.EXPLANATION,
    )[1]["content"]

    assert '"constraints_remove":["c_003"]' in dynamic
    assert '"code":"rerank_degraded"' in dynamic


def test_understand_schema_order_enums_and_no_unique_items() -> None:
    schema = understand_guided_schema(
        ["spot_001", "spot_002"],
        ["c_001", "c_002"],
    )

    assert list(schema["properties"]) == [
        "references",
        "constraints",
        "constraints_remove",
        "selection_hints",
        "unmodeled",
        "intent",
        "plan",
    ]
    assert not _contains_key(schema, "uniqueItems")
    assert schema["properties"]["plan"]["maxItems"] == 3
    reference_enum = schema["properties"]["references"]["items"]["properties"]["spot_id"]["enum"]
    assert reference_enum == ["spot_001", "spot_002"]
    assert schema["properties"]["constraints_remove"]["items"]["enum"] == [
        "c_001",
        "c_002",
    ]

    empty_vocab = understand_guided_schema([])
    assert empty_vocab["properties"]["references"]["maxItems"] == 0
    assert empty_vocab["properties"]["constraints_remove"]["maxItems"] == 0
    assert not _contains_key(empty_vocab, "pattern")

    tool_values = [
        tool
        for variant in schema["properties"]["plan"]["items"]["anyOf"]
        for tool in variant["properties"]["tool"]["enum"]
    ]
    assert tool_values == [
        "recommend",
        "plan_itinerary",
        "edit_itinerary",
        "search_knowledge",
        "ask_user",
    ]


def test_understand_schema_constrains_only_ask_user_args() -> None:
    schema = understand_guided_schema(["spot_001"])
    regular_step, ask_user_step = schema["properties"]["plan"]["items"]["anyOf"]

    assert regular_step["properties"]["tool"]["enum"] == [
        "recommend",
        "plan_itinerary",
        "edit_itinerary",
        "search_knowledge",
    ]
    assert regular_step["properties"]["args"] == {
        "type": "object",
        "additionalProperties": True,
    }

    assert ask_user_step["properties"]["tool"]["enum"] == ["ask_user"]
    args = ask_user_step["properties"]["args"]
    assert args["required"] == ["kind", "reason", "options"]
    assert args["additionalProperties"] is False
    assert args["properties"]["kind"]["enum"] == ["preference", "clarify"]
    assert args["properties"]["reason"]["minLength"] == 1

    options = args["properties"]["options"]
    assert options["minItems"] == 2
    assert options["maxItems"] == 4
    assert options["items"]["required"] == ["label", "value"]
    assert options["items"]["additionalProperties"] is False
    assert options["items"]["properties"]["label"]["minLength"] == 1
    assert options["items"]["properties"]["value"]["minLength"] == 1
    assert not _contains_key(schema, "uniqueItems")


def test_recommendation_context_uses_same_turn_profile_and_score_delta() -> None:
    state = _state()
    state.profile = ProfileState(party="solo", interests={"nature": 0.2})
    state.profile_delta = ProfileDelta.model_validate(
        {"party": "family_kids", "interests": {"water": 0.8}}
    )
    state.score_adjustments = [
        ScoreAdjustment(spot_id="spot_001", delta=0.4, why="静か")
    ]

    context = build_recommendation_context(state)

    assert context.profile.party.value == "family_kids"
    assert context.profile.interests["nature"] == 0.2
    assert context.profile.interests["water"] == 0.8
    assert context.score_adjustments == {"spot_001": 0.4}


def test_first_itinerary_state_merges_pending_and_turn_constraints() -> None:
    state = _state()
    state.pending_constraints = [
        {
            "id": "c_001",
            "pred": "lunch_break",
            "args": {"from": 690, "to": 810, "min": 60},
            "weight": 0.8,
            "source_text": "昼休憩",
            "handling": "dsl",
        }
    ]
    state.constraints = [
        ConstraintDraft(
            id="c_002",
            pred="max_leg_min",
            args={"n": 20},
            source_text="近く",
        )
    ]

    merged = _merged_constraints(state)

    assert [value["id"] for value in merged] == ["c_001", "c_002"]


class _SnapshotRepository:
    def __init__(self, snapshot: ContextSnapshot) -> None:
        self.snapshot = snapshot

    async def load_snapshot(self, user_id: int) -> ContextSnapshot:
        assert user_id == 1
        return self.snapshot


async def test_interpretation_chip_resolution_is_accepted_without_spot_vocab() -> None:
    spot = _state().spot_catalog["spot_001"]
    pending = {
        "kind": "clarify",
        "surface": "全部",
        "reason": "範囲が曖昧です",
        "options": [
            {
                "label": "候補をすべて",
                "value": "all_matches",
            },
            {
                "label": "1件だけ",
                "value": "single_match",
            },
        ],
        "original_utterance": "全部外して",
        "asked_at_message_id": 10,
    }
    snapshot = ContextSnapshot(
        thread_id=1,
        profile=ProfileState(),
        itinerary=None,
        messages=[],
        last_candidates=[],
        presented_spot_ids=[],
        asked_slots=[],
        ask_streak=0,
        pending_ask=pending,
        resolved_ambiguities=[],
        pending_constraints=[],
        realtime={},
        spots={spot.spot_id: spot},
        tag_vocabulary=["自然", "登山", "鳥海山"],
    )

    state = await load_context(
        _SnapshotRepository(snapshot),
        user_id=1,
        utterance="候補をすべて",
        resolves={"surface": "全部", "value": "all_matches"},
    )

    assert state.tool_results == [
        {
            "tool": "ask_user",
            "input": {
                "kind": "clarify",
                "reason": "範囲が曖昧です",
                "options": [
                    {"label": "候補をすべて", "value": "all_matches"},
                    {"label": "1件だけ", "value": "single_match"},
                ],
                "surface": "全部",
                "original_utterance": "全部外して",
                "asked_at_message_id": 10,
            },
            "output": {
                "answer": "all_matches",
                "answered_by": "chip",
                "surface": "全部",
            },
        }
    ]
    assert state.pending_ask is None
    assert state.log_fields["resumed_from_ask"] is True
    assert state.tag_vocabulary == ["自然", "登山", "鳥海山"]
    prompt = build_understand_messages(state)[1]["content"]
    assert '"tool_results":[{"tool":"ask_user"' in prompt
    assert "自然 | 登山 | 鳥海山" in prompt


async def test_pending_ask_without_resolves_returns_free_text_tool_result() -> None:
    spot = _state().spot_catalog["spot_001"]
    snapshot = ContextSnapshot(
        thread_id=1,
        profile=ProfileState(),
        itinerary=None,
        messages=[],
        last_candidates=[],
        presented_spot_ids=[],
        asked_slots=["pace"],
        ask_streak=1,
        pending_ask={
            "kind": "preference",
            "slot": "pace",
            "reason": "希望のペースを確認します",
            "options": [
                {"label": "ゆったり", "value": "relaxed"},
                {"label": "多め", "value": "packed"},
            ],
        },
        resolved_ambiguities=[],
        pending_constraints=[],
        realtime={},
        spots={spot.spot_id: spot},
    )

    state = await load_context(
        _SnapshotRepository(snapshot),
        user_id=1,
        utterance="かなりゆっくり回りたい",
    )

    assert state.tool_results[0]["output"] == {
        "answer": "かなりゆっくり回りたい",
        "answered_by": "free_text",
        "slot": "pace",
    }
