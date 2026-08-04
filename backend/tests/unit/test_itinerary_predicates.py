"""制約 DSL 17 種の違反量と fail-safe を検証する。"""

from collections.abc import Sequence

import pytest

from app.domains.itinerary.predicates import (
    PENALTIES,
    normalize_constraints,
    predicate_context,
)
from app.domains.itinerary.types import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    LegFromPrev,
    PredEnum,
    SpotEndpoint,
)

SPOTS = {
    "spot_a": {"tags_ja": ["滝", "自然"]},
    "spot_b": {"tags_ja": ["神社"]},
    "spot_c": {"tags_ja": ["滝"]},
    "spot_d": {"tags_ja": ["温泉"]},
}


def _item(
    spot_id: str,
    *,
    arrive: int = 600,
    stay: int = 30,
    leg: int = 10,
    mode: str = "car",
) -> ItineraryItem:
    return ItineraryItem(
        seq=1,
        spot_id=spot_id,
        arrive_min=arrive,
        stay_min=stay,
        depart_min=arrive + stay,
        leg_from_prev=LegFromPrev(mode=mode, min=leg),
    )


def _itinerary(*days: Sequence[ItineraryItem]) -> Itinerary:
    return Itinerary(
        days=[
            ItineraryDay(
                date=f"2026-08-{10 + index:02d}",
                start_min=540,
                end_min=1020,
                origin=SpotEndpoint(spot_id="spot_d"),
                destination=SpotEndpoint(spot_id="spot_d"),
                items=[item.model_copy(update={"seq": seq}) for seq, item in enumerate(items, 1)],
            )
            for index, items in enumerate(days)
        ],
        version=1,
    )


CASES = [
    ("weight", {"target": "滝", "w": 2}, _itinerary([_item("spot_a")]), _itinerary([]), -2, 0),
    ("require", {"target": "滝"}, _itinerary([_item("spot_a")]), _itinerary([]), 0, 1),
    ("exclude", {"target": "滝"}, _itinerary([]), _itinerary([_item("spot_a")]), 0, 1),
    (
        "count_at_most",
        {"target": "滝", "n": 1},
        _itinerary([_item("spot_a")]),
        _itinerary([_item("spot_a"), _item("spot_c")]),
        0,
        1,
    ),
    (
        "count_at_least",
        {"target": "滝", "n": 2},
        _itinerary([_item("spot_a"), _item("spot_c")]),
        _itinerary([_item("spot_a")]),
        0,
        1,
    ),
    (
        "first",
        {"target": "神社"},
        _itinerary([_item("spot_b"), _item("spot_a")]),
        _itinerary([_item("spot_a"), _item("spot_b")]),
        0,
        1,
    ),
    (
        "last",
        {"target": "神社"},
        _itinerary([_item("spot_a"), _item("spot_b")]),
        _itinerary([_item("spot_b"), _item("spot_a")]),
        0,
        1,
    ),
    (
        "before",
        {"a": "滝", "b": "神社"},
        _itinerary([_item("spot_a"), _item("spot_b")]),
        _itinerary([_item("spot_b"), _item("spot_a")]),
        0,
        1,
    ),
    (
        "not_consecutive",
        {"target": "滝"},
        _itinerary([_item("spot_a"), _item("spot_b"), _item("spot_c")]),
        _itinerary([_item("spot_a"), _item("spot_c")]),
        0,
        1,
    ),
    (
        "same_day",
        {"a": "滝", "b": "神社"},
        _itinerary([_item("spot_a"), _item("spot_b")]),
        _itinerary([_item("spot_a")], [_item("spot_b")]),
        0,
        1,
    ),
    (
        "different_day",
        {"a": "滝", "b": "神社"},
        _itinerary([_item("spot_a")], [_item("spot_b")]),
        _itinerary([_item("spot_a"), _item("spot_b")]),
        0,
        1,
    ),
    (
        "time_window",
        {"target": "滝", "from": 590, "to": 640},
        _itinerary([_item("spot_a", arrive=600, stay=30)]),
        _itinerary([_item("spot_a", arrive=570, stay=90)]),
        0,
        40,
    ),
    (
        "stay_at_least",
        {"target": "滝", "min": 30},
        _itinerary([_item("spot_a", stay=30)]),
        _itinerary([_item("spot_a", stay=10)]),
        0,
        20,
    ),
    (
        "day_part_load",
        {"day": 1, "part": "morning", "level": "low"},
        _itinerary([_item("spot_a", arrive=550), _item("spot_b", arrive=600)]),
        _itinerary(
            [_item("spot_a", arrive=550), _item("spot_b", arrive=600), _item("spot_c", arrive=650)]
        ),
        0,
        1,
    ),
    (
        "max_leg_min",
        {"n": 30},
        _itinerary([_item("spot_a", leg=30)]),
        _itinerary([_item("spot_a", leg=45)]),
        0,
        15,
    ),
    (
        "mode_pref",
        {"mode": "foot"},
        _itinerary([_item("spot_a", mode="foot")]),
        _itinerary([_item("spot_a", mode="car")]),
        0,
        1,
    ),
    (
        "lunch_break",
        {"from": 720, "to": 780, "min": 30},
        _itinerary([]),
        _itinerary([_item("spot_a", arrive=750, stay=30, leg=30)]),
        0,
        1,
    ),
]


@pytest.mark.parametrize(
    ("pred", "args", "satisfied", "violated", "expected_ok", "expected_bad"),
    CASES,
)
def test_each_predicate_has_satisfied_and_violated_case(
    pred: str,
    args: dict[str, object],
    satisfied: Itinerary,
    violated: Itinerary,
    expected_ok: float,
    expected_bad: float,
) -> None:
    with predicate_context(SPOTS):
        ok_value, ok_message = PENALTIES[PredEnum(pred)](satisfied, args)
        bad_value, bad_message = PENALTIES[PredEnum(pred)](violated, args)

    assert ok_value == pytest.approx(expected_ok)
    assert bad_value == pytest.approx(expected_bad)
    assert ok_message
    assert bad_message


def test_registry_contains_exactly_17_distinct_predicates() -> None:
    assert len(PredEnum) == 17
    assert set(PENALTIES) == set(PredEnum)
    assert len({id(function) for function in PENALTIES.values()}) == 17


def test_unknown_predicate_and_missing_target_become_unmodeled() -> None:
    result = normalize_constraints(
        [
            {"pred": "future_pred", "args": {}},
            {"pred": "require", "args": {"target": "spot_missing"}},
            {
                "pred": "time_window",
                "args": {"target": "滝", "from": "09:30", "to": "11:00"},
            },
        ],
        SPOTS,
        created_at_version=1,
    )

    assert len(result.constraints) == 1
    assert result.constraints[0].args["from"] == 570
    assert result.constraints[0].args["to"] == 660
    assert [item.pred for item in result.unmodeled] == ["future_pred", "require"]
    assert all(item.handling == "unmodeled" for item in result.unmodeled)
