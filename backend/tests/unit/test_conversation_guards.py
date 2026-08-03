"""P1〜P8・G1〜G9 と横断ガードの層 1 仕様。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.domains.conversation.guards import (
    normalize_revert_ops,
    validate_ask_user,
    validate_classification_completeness,
)
from app.domains.conversation.planner import _cyclic_step_ids, validate_plan
from app.domains.conversation.state import (
    ItineraryState,
    ProfileState,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import (
    AskUserArgs,
    ConstraintDraft,
    Intent,
    PlanStep,
    ScoreAdjustment,
    SelectionHint,
    UnmodeledItem,
)
from app.domains.itinerary.types import Itinerary


def _state(
    plan: list[PlanStep],
    *,
    itinerary_version: int | None = None,
    intent: Intent = Intent.EDIT,
) -> TurnState:
    spots = {
        f"spot_{number:03d}": SpotFact(
            spot_id=f"spot_{number:03d}",
            name_ja=f"地点{number}",
            kind="facility" if number == 1 else "poi",
            tags_ja=["滝"] if number in {2, 3} else ["自然"],
        )
        for number in range(1, 7)
    }
    itinerary = (
        ItineraryState(
            itinerary=Itinerary(days=[], version=itinerary_version),
            constraints=[],
            parent_version=(itinerary_version - 1 if itinerary_version > 1 else None),
        )
        if itinerary_version is not None
        else None
    )
    return TurnState(
        turn_id="turn-test",
        thread_id=1,
        user_id=1,
        utterance="test",
        profile=ProfileState(),
        itinerary=itinerary,
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
        default_origin_spot_id="spot_001",
        intent=intent,
        plan=plan,
    )


def _search(step_id: int) -> PlanStep:
    return PlanStep(
        id=step_id,
        tool="search_knowledge",
        args={"request": f"質問 {step_id}"},
    )


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


async def test_p2_to_p6_run_before_p1_so_valid_fourth_step_survives() -> None:
    state = _state(
        [
            PlanStep(id=1, tool="unknown", args={}),
            _search(2),
            _search(3),
            _search(4),
            _search(5),
        ]
    )

    await validate_plan(state)

    assert [step.id for step in state.accepted_steps] == [2, 3, 4]
    assert [(value.step_id, value.rule) for value in state.rejected_steps] == [
        (1, "P2"),
        (5, "P1"),
    ]


@pytest.mark.parametrize(
    ("plan", "rule"),
    [
        ([PlanStep(id=1, tool="recommend", args={"k": 99})], "P3"),
        (
            [
                PlanStep(
                    id=1,
                    tool="edit_itinerary",
                    args={"ops": [{"op": "add", "targets": "$2.spot_ids"}]},
                ),
                PlanStep(id=2, tool="recommend", args={"k": 2}),
            ],
            "P4",
        ),
        (
            [
                PlanStep(
                    id=1,
                    tool="ask_user",
                    args=_preference_args(),
                ),
                _search(2),
            ],
            "P5",
        ),
        (
            [
                PlanStep(id=1, tool="edit_itinerary", args={"ops": []}),
                PlanStep(id=2, tool="edit_itinerary", args={"ops": []}),
            ],
            "P6",
        ),
    ],
)
async def test_plan_rules_leave_reason_in_rejected_steps(
    plan: list[PlanStep], rule: str
) -> None:
    state = _state(plan, itinerary_version=2)

    await validate_plan(state)

    assert any(value.rule == rule for value in state.rejected_steps)


async def test_p5_allows_ask_user_only_at_tail_and_at_most_once() -> None:
    not_at_tail = _state(
        [
            PlanStep(id=1, tool="ask_user", args=_preference_args()),
            _search(2),
        ]
    )
    duplicated = _state(
        [
            PlanStep(id=1, tool="ask_user", args=_preference_args("pace")),
            PlanStep(id=2, tool="ask_user", args=_preference_args("mobility")),
        ]
    )

    await validate_plan(not_at_tail)
    await validate_plan(duplicated)

    assert [step.tool for step in not_at_tail.accepted_steps] == ["search_knowledge"]
    assert any(value.rule == "P5" for value in not_at_tail.rejected_steps)
    assert sum(step.tool == "ask_user" for step in duplicated.accepted_steps) <= 1
    assert any(value.rule == "G1" for value in duplicated.rejected_steps)


async def test_closed_world_and_edit_precondition_are_rejected() -> None:
    hallucinated = _state(
        [
            PlanStep(
                id=1,
                tool="search_knowledge",
                args={"request": "説明", "spot_id": "spot_999"},
            )
        ]
    )
    no_itinerary = _state(
        [PlanStep(id=1, tool="edit_itinerary", args={"ops": []})]
    )

    await validate_plan(hallucinated)
    await validate_plan(no_itinerary)

    assert hallucinated.accepted_steps == []
    assert "実在しない" in hallucinated.rejected_steps[0].reason
    assert no_itinerary.accepted_steps == []
    assert "旅程" in no_itinerary.rejected_steps[0].reason


async def test_p4_accepts_typed_backward_reference_and_p7_assigns_referenced_step() -> None:
    state = _state(
        [
            PlanStep(
                id=1,
                tool="recommend",
                args={"filter": {"tags": ["滝"]}, "k": 2},
            ),
            PlanStep(
                id=2,
                tool="edit_itinerary",
                args={"ops": [{"op": "add", "targets": "$1.spot_ids"}]},
            ),
        ],
        itinerary_version=2,
    )

    await validate_plan(state)

    assert [value.id for value in state.accepted_steps] == [1, 2]
    assert state.llm_budget_step == 1


async def test_p4_rejects_reference_from_tool_without_requested_output_type() -> None:
    state = _state(
        [
            _search(1),
            PlanStep(
                id=2,
                tool="edit_itinerary",
                args={"ops": [{"op": "add", "targets": "$1.spot_ids"}]},
            ),
        ],
        itinerary_version=2,
    )

    await validate_plan(state)

    assert [value.id for value in state.accepted_steps] == [1]
    assert state.rejected_steps[0].rule == "P4"
    assert "spot_ids" in state.rejected_steps[0].reason


def test_p4_cycle_detector_covers_future_reference_vocabulary() -> None:
    cyclic = [
        PlanStep(
            id=1,
            tool="edit_itinerary",
            args={"ops": [{"op": "add", "targets": "$2.spot_ids"}]},
        ),
        PlanStep(
            id=2,
            tool="edit_itinerary",
            args={"ops": [{"op": "add", "targets": "$1.spot_ids"}]},
        ),
    ]

    assert _cyclic_step_ids(cyclic) == {1, 2}


async def test_p7_does_not_count_search_knowledge_and_p8_keeps_no_tools() -> None:
    state = _state([_search(1), PlanStep(id=2, tool="recommend", args={"k": 2})])
    empty = _state([PlanStep(id=1, tool="unknown", args={})])

    await validate_plan(state)
    await validate_plan(empty)

    assert state.llm_budget_step == 2
    assert empty.accepted_steps == []
    assert empty.llm_budget_step is None


async def test_g4_planner_adds_result_before_preference_question() -> None:
    state = _state(
        [
            PlanStep(
                id=1,
                tool="ask_user",
                args=_preference_args(),
            )
        ],
        intent=Intent.RECOMMEND,
    )
    state.profile = ProfileState(party="solo")

    await validate_plan(state)

    assert [value.tool for value in state.accepted_steps] == [
        "recommend",
        "ask_user",
    ]


async def test_revert_requires_version_two_and_discards_other_ops() -> None:
    normalized, changed = normalize_revert_ops(
        [{"op": "add", "targets": ["spot_002"]}, {"op": "revert"}]
    )
    state = _state(
        [PlanStep(id=1, tool="edit_itinerary", args={"ops": normalized})],
        itinerary_version=1,
    )

    await validate_plan(state)

    assert changed is True
    assert normalized == [{"op": "revert"}]
    assert state.accepted_steps == []
    assert "戻せる" in state.rejected_steps[0].reason

    mixed = _state(
        [
            PlanStep(
                id=1,
                tool="edit_itinerary",
                args={
                    "ops": [
                        {"op": "add", "targets": ["spot_002"]},
                        {"op": "revert"},
                    ]
                },
            )
        ],
        itinerary_version=2,
    )

    await validate_plan(mixed)

    assert mixed.accepted_steps[0].args["ops"] == [{"op": "revert", "to_version": None}]
    assert any(value.rule == "revert_exclusive" for value in mixed.rejected_steps)


def test_g1_to_g5() -> None:
    base = AskUserArgs.model_validate(_preference_args())
    profile = ProfileState(party="solo")

    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=0,
        intent=Intent.EDIT,
        profile=profile,
        has_non_question_step=False,
        question_count=2,
    ).rule == "G1"
    assert validate_ask_user(
        base,
        asked_slots=["pace"],
        ask_streak=0,
        intent=Intent.EDIT,
        profile=profile,
        has_non_question_step=False,
    ).rule == "G2"
    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=2,
        intent=Intent.EDIT,
        profile=profile,
        has_non_question_step=False,
    ).rule == "G3"
    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=0,
        intent=Intent.RECOMMEND,
        profile=profile,
        has_non_question_step=False,
    ).rule == "G4"
    one_option = _preference_args()
    one_option["options"] = [{"label": "1つだけ", "value": "one"}]
    assert validate_ask_user(
        AskUserArgs.model_validate(one_option),
        asked_slots=[],
        ask_streak=0,
        intent=Intent.EDIT,
        profile=profile,
        has_non_question_step=False,
    ).rule == "G5"
    assert validate_ask_user(
        base,
        asked_slots=[],
        ask_streak=0,
        intent=Intent.EDIT,
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
        "intent": Intent.UNCLEAR,
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


def test_classification_completeness_counts_all_four_routes() -> None:
    values = {
        "constraints": [ConstraintDraft(pred="require", args={"target": "spot_001"})],
        "score_adjustments": [ScoreAdjustment(spot_id="spot_001", delta=0.2)],
        "selection_hints": [SelectionHint(text="のんびり")],
        "unmodeled": [UnmodeledItem(text="屋台")],
    }

    accepted = validate_classification_completeness(extracted_count=4, **values)
    rejected = validate_classification_completeness(extracted_count=5, **values)

    assert accepted.accepted is True
    assert rejected.accepted is False


async def test_plan_defaults_are_visible_and_add_confirmation_after_plan() -> None:
    state = _state(
        [PlanStep(id=1, tool="plan_itinerary", args={"days": []})],
        intent=Intent.PLAN,
    )

    await validate_plan(state, now=datetime(2026, 8, 2, 9, 0))

    assert [value.tool for value in state.accepted_steps] == [
        "plan_itinerary",
        "ask_user",
    ]
    day = state.accepted_steps[0].args["days"][0]
    assert day["date"] == "2026-08-03"
    assert day["start"] == "09:00"
    assert day["end"] == "17:00"
    assert state.assumptions


@pytest.mark.parametrize(
    ("origin", "destination", "expected_origin", "expected_destination"),
    [
        (
            {"kind": "facility", "id": "spot_001"},
            {"kind": "spot", "id": "spot_002"},
            {"kind": "facility", "id": "spot_001"},
            {"kind": "spot", "id": "spot_002"},
        ),
        (
            "spot_001",
            "spot_002",
            {"kind": "facility", "id": "spot_001"},
            {"kind": "spot", "id": "spot_002"},
        ),
    ],
)
async def test_p3_accepts_or_normalizes_plan_endpoints(
    origin: object,
    destination: object,
    expected_origin: dict[str, str],
    expected_destination: dict[str, str],
) -> None:
    state = _state(
        [
            PlanStep(
                id=1,
                tool="plan_itinerary",
                args={
                    "days": [
                        {
                            "date": "2026-08-03",
                            "start": "09:00",
                            "end": "17:00",
                            "origin": origin,
                            "destination": destination,
                        }
                    ]
                },
            )
        ],
        intent=Intent.PLAN,
    )

    await validate_plan(state, now=datetime(2026, 8, 2, 9, 0))

    assert state.rejected_steps == []
    assert state.accepted_steps[0].args["days"][0]["origin"] == expected_origin
    assert (
        state.accepted_steps[0].args["days"][0]["destination"]
        == expected_destination
    )


async def test_p3_normalizes_direct_remove_target_to_array() -> None:
    state = _state(
        [
            PlanStep(
                id=1,
                tool="edit_itinerary",
                args={"ops": [{"op": "remove", "targets": "spot_002"}]},
            )
        ],
        itinerary_version=2,
    )

    await validate_plan(state)

    assert state.rejected_steps == []
    assert state.accepted_steps[0].args["ops"] == [
        {"op": "remove", "targets": ["spot_002"]}
    ]
