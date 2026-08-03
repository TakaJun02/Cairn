"""制約検証・revert 排他化・応答クローズドワールド・反復検知と、

`ask_user` 抑制ガード(R4・A1〜A5。`Docs/30_design/agent_react_architecture.md`
§10)の層 1 仕様。旧 P1〜P8(一括プラン検証。`planner.py`)は ReAct 化で
廃止した。旧 G1〜G9(ADR-0018 の中断・復帰方式の抑制ガード)は
`evaluate_ask_user` へ作り替えた(ADR-0019・§7)。
"""

from __future__ import annotations

from app.domains.conversation.guards import (
    evaluate_ask_user,
    has_repeated_ngram,
    normalize_revert_ops,
    validate_and_normalize_constraints,
    validate_response_spot_names,
)
from app.domains.conversation.state import SpotFact
from app.domains.conversation.types import AskUserArgs, ConstraintDraft


def _spots() -> dict[str, SpotFact]:
    return {
        f"spot_{number:03d}": SpotFact(
            spot_id=f"spot_{number:03d}",
            name_ja=f"地点{number}",
            kind="facility" if number == 1 else "poi",
            tags_ja=["滝"] if number in {2, 3} else ["自然"],
        )
        for number in range(1, 7)
    }


def _preference_args(slot: str = "pace") -> dict[str, object]:
    return {
        "kind": "preference",
        "slot": slot,
        "reason": "選好を確認します",
        "options": [
            {"label": "ゆったり", "value": "relaxed"},
            {"label": "多め", "value": "packed"},
        ],
    }


def test_repeated_ngram_detector_handles_tokens_and_no_space_text() -> None:
    assert has_repeated_ngram("同じブロックを反復します。" * 8) is True
    assert has_repeated_ngram("普通の短い JSON です") is False


def test_valid_constraint_is_normalized_with_assigned_id() -> None:
    result = validate_and_normalize_constraints(
        [ConstraintDraft(pred="require", args={"target": "spot_002"}, source_text="滝")],
        _spots(),
        created_at_version=1,
    )

    assert len(result.constraints) == 1
    assert result.constraints[0].id.startswith("c_")
    assert result.unmodeled == ()


def test_unknown_predicate_and_invalid_target_become_unmodeled() -> None:
    result = validate_and_normalize_constraints(
        [
            ConstraintDraft(pred="unknown_pred", args={}, source_text="屋台"),
            ConstraintDraft(
                pred="require", args={"target": "spot_999"}, source_text="架空"
            ),
        ],
        _spots(),
        created_at_version=1,
    )

    assert result.constraints == ()
    assert [value.text for value in result.unmodeled] == ["屋台", "架空"]
    assert all(value.reason for value in result.unmodeled)


def test_constraint_ids_do_not_collide_with_used_ids() -> None:
    result = validate_and_normalize_constraints(
        [ConstraintDraft(pred="require", args={"target": "spot_002"})],
        _spots(),
        created_at_version=1,
        used_ids={"c_001"},
    )

    assert result.constraints[0].id != "c_001"


def test_revert_requires_exclusive_ops() -> None:
    normalized, changed = normalize_revert_ops(
        [{"op": "add", "targets": ["spot_002"]}, {"op": "revert"}]
    )

    assert changed is True
    assert normalized == [{"op": "revert"}]

    normalized_alone, changed_alone = normalize_revert_ops([{"op": "revert"}])
    assert changed_alone is False
    assert normalized_alone == [{"op": "revert"}]


def test_validate_response_spot_names_rejects_unpresented_names() -> None:
    all_names = {"spot_001": "鶴間池", "spot_002": "元滝伏流水"}

    accepted = validate_response_spot_names(
        "鶴間池をご案内します。",
        all_spot_names=all_names,
        allowed_spot_ids={"spot_001"},
    )
    rejected = validate_response_spot_names(
        "元滝伏流水も良いですよ。",
        all_spot_names=all_names,
        allowed_spot_ids={"spot_001"},
    )

    assert accepted.accepted is True
    assert rejected.accepted is False
    assert rejected.rule == "closed_world_response"


def test_r4_and_a1_a2_a3_reject_preference_questions() -> None:
    base = AskUserArgs.model_validate(_preference_args())

    assert evaluate_ask_user(
        base, ask_user_count=2, ask_streak=0, asked_slots=[]
    ).rule == "R4"
    assert evaluate_ask_user(
        base, ask_user_count=0, ask_streak=2, asked_slots=[]
    ).rule == "A2"
    assert evaluate_ask_user(
        base, ask_user_count=0, ask_streak=0, asked_slots=["pace"]
    ).rule == "A1"
    one_option = _preference_args()
    one_option["options"] = [{"label": "1つだけ", "value": "one"}]
    assert evaluate_ask_user(
        AskUserArgs.model_validate(one_option),
        ask_user_count=0,
        ask_streak=0,
        asked_slots=[],
    ).rule == "A3"
    # 通常時は受理される。
    assert evaluate_ask_user(
        base, ask_user_count=0, ask_streak=0, asked_slots=[]
    ).accepted is True


def _clarification(*, invalid_value: str | None = None) -> AskUserArgs:
    return AskUserArgs.model_validate(
        {
            "kind": "clarify",
            "surface": "2番目",
            "reason": "候補が複数あります",
            "options": [
                {
                    "label": f"地点{index}",
                    "value": (
                        invalid_value
                        if index == 2 and invalid_value
                        else f"spot_{index:03d}"
                    ),
                }
                for index in range(1, 3)
            ],
        }
    )


def test_a4_and_a5_apply_to_clarify_only() -> None:
    existing = {"spot_001", "spot_002", "spot_003", "spot_004", "spot_005"}
    common = {"ask_user_count": 0, "ask_streak": 0, "asked_slots": []}

    assert evaluate_ask_user(
        _clarification(invalid_value="spot_999"),
        **common,
        allowed_spot_ids=existing,
        existing_spot_ids=existing,
    ).rule == "A4"
    assert evaluate_ask_user(
        _clarification(),
        **common,
        resolved_ambiguities=[{"surface": "2番目"}],
    ).rule == "A5"
    # allowed/existing_spot_ids を渡さない呼び出し元(recommend SA・知識検索
    # SA)では A4 を検査しない(値の意味が spot_id とは限らないため)。
    assert evaluate_ask_user(
        _clarification(invalid_value="not-a-spot-id"), **common
    ).accepted is True
    # 正常系は受理される。
    assert evaluate_ask_user(
        _clarification(), **common, allowed_spot_ids=existing, existing_spot_ids=existing
    ).accepted is True
