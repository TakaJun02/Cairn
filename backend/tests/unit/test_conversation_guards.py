"""段2で残したガード(制約検証・revert 排他化・応答クローズドワールド・

反復検知)と、段5用に残す `ask_user` 抑制ガード(G1〜G9)の層 1 仕様。
旧 P1〜P8(一括プラン検証。`planner.py`)は ReAct 化で廃止した。
"""

from __future__ import annotations

from app.domains.conversation.guards import (
    has_repeated_ngram,
    normalize_revert_ops,
    validate_and_normalize_constraints,
    validate_ask_user,
    validate_response_spot_names,
)
from app.domains.conversation.state import ProfileState, SpotFact
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


def test_g1_to_g5() -> None:
    base = AskUserArgs.model_validate(_preference_args())
    profile = ProfileState(party="solo")

    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=0,
        intent="edit",
        profile=profile,
        has_non_question_step=False,
        question_count=2,
    ).rule == "G1"
    assert validate_ask_user(
        base,
        asked_slots=["pace"],
        ask_streak=0,
        intent="edit",
        profile=profile,
        has_non_question_step=False,
    ).rule == "G2"
    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=2,
        intent="edit",
        profile=profile,
        has_non_question_step=False,
    ).rule == "G3"
    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=0,
        intent="recommend",
        profile=profile,
        has_non_question_step=False,
    ).rule == "G4"
    one_option = _preference_args()
    one_option["options"] = [{"label": "1つだけ", "value": "one"}]
    assert validate_ask_user(
        AskUserArgs.model_validate(one_option),
        asked_slots=[],
        ask_streak=0,
        intent="edit",
        profile=profile,
        has_non_question_step=False,
    ).rule == "G5"
    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=0,
        intent="edit",
        profile=profile,
        has_non_question_step=False,
        has_viable_plan=True,
    ).rule == "G9"


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


def test_g3_g6_g7_and_g9_apply_to_unified_ask_user() -> None:
    existing = {"spot_001", "spot_002", "spot_003", "spot_004", "spot_005"}
    common = {
        "asked_slots": [],
        "intent": "unclear",
        "profile": ProfileState(),
        "has_non_question_step": False,
        "allowed_spot_ids": existing,
        "existing_spot_ids": existing,
    }
    assert validate_ask_user(
        _clarification(),
        **common,
        resolved_ambiguities=[],
        ask_streak=2,
        has_viable_plan=False,
    ).rule == "G3"
    assert validate_ask_user(
        _clarification(invalid_value="spot_999"),
        **common,
        resolved_ambiguities=[],
        ask_streak=0,
        has_viable_plan=False,
    ).rule == "G6"
    assert validate_ask_user(
        _clarification(),
        **common,
        resolved_ambiguities=[{"surface": "2番目"}],
        ask_streak=0,
        has_viable_plan=False,
    ).rule == "G7"
    assert validate_ask_user(
        _clarification(),
        **common,
        resolved_ambiguities=[],
        ask_streak=0,
        has_viable_plan=True,
    ).rule == "G9"
