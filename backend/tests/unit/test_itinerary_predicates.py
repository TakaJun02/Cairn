"""制約 DSL 17 種の違反量と fail-safe を検証する。"""

from collections.abc import Sequence

import pytest

from app.domains.conversation.guards import _FORBIDDEN_SPOT_ID_RE
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


# ---------------------------------------------------------------------------
# [25 §1-7]: 譲歩メッセージは spot_id を露出せず表示名で組む。
# ---------------------------------------------------------------------------


def test_require_message_uses_display_name_when_target_is_spot_id() -> None:
    spots = {"spot_014": {"tags_ja": ["滝"], "name_ja": "元滝伏流水"}}
    with predicate_context(spots):
        violation, message = PENALTIES[PredEnum.REQUIRE](
            _itinerary([]), {"target": "spot_014"}
        )

    assert violation == 1.0
    assert "元滝伏流水" in message
    assert "spot_014" not in message


def test_require_message_keeps_tag_name_unchanged() -> None:
    with predicate_context(SPOTS):
        _, message = PENALTIES[PredEnum.REQUIRE](_itinerary([]), {"target": "滝"})

    assert "滝" in message


def test_require_message_falls_back_to_neutral_label_when_name_ja_is_empty() -> None:
    spots = {"spot_014": {"tags_ja": ["滝"], "name_ja": ""}}
    with predicate_context(spots):
        _, message = PENALTIES[PredEnum.REQUIRE](_itinerary([]), {"target": "spot_014"})

    assert "指定の場所" in message
    assert "spot_014" not in message


def test_require_message_falls_back_to_neutral_label_when_name_ja_is_absent() -> None:
    """`name_ja` キー自体が無い(素の Mapping)スポットでも id へフォールバックしない。"""

    with predicate_context(SPOTS):
        _, message = PENALTIES[PredEnum.REQUIRE](_itinerary([]), {"target": "spot_a"})

    assert "指定の場所" in message
    assert "spot_a" not in message


def test_before_message_uses_display_names_for_a_and_b() -> None:
    spots = {
        "spot_a": {"tags_ja": [], "name_ja": "元滝伏流水"},
        "spot_b": {"tags_ja": [], "name_ja": "丸池様"},
    }
    with predicate_context(spots):
        _, message = PENALTIES[PredEnum.BEFORE](
            _itinerary([]), {"a": "spot_a", "b": "spot_b"}
        )

    assert "元滝伏流水" in message
    assert "丸池様" in message
    assert "spot_a" not in message
    assert "spot_b" not in message


def test_same_day_message_uses_display_names_for_a_and_b() -> None:
    spots = {
        "spot_a": {"tags_ja": [], "name_ja": "元滝伏流水"},
        "spot_b": {"tags_ja": [], "name_ja": "丸池様"},
    }
    with predicate_context(spots):
        _, message = PENALTIES[PredEnum.SAME_DAY](
            _itinerary([]), {"a": "spot_a", "b": "spot_b"}
        )

    assert "元滝伏流水" in message
    assert "丸池様" in message
    assert "spot_a" not in message
    assert "spot_b" not in message


# ---------------------------------------------------------------------------
# F10([25 §1-7] レビュー是正): PENALTIES 網羅テスト。target/a/b を引数に
# 取る述語(13 種。not_consecutive を含む)は、それらに spot_id を渡しても
# message_ja に spot_id トークンを露出しない(`predicates._display` が
# 名前解決/中立表記に落とすため)。day_part_load/max_leg_min/mode_pref/
# lunch_break の 4 述語は message_ja が target/a/b を参照しない(仕様上
# spot_id を含み得ない)ためスキップする。
# ---------------------------------------------------------------------------

_TARGET_ARGS_BY_PRED: dict[str, dict[str, object]] = {
    "weight": {"target": "spot_t1", "w": 1},
    "require": {"target": "spot_t1"},
    "exclude": {"target": "spot_t1"},
    "count_at_most": {"target": "spot_t1", "n": 0},
    "count_at_least": {"target": "spot_t1", "n": 1},
    "first": {"target": "spot_t1"},
    "last": {"target": "spot_t1"},
    "not_consecutive": {"target": "spot_t1"},
    "time_window": {"target": "spot_t1", "from": 540, "to": 600},
    "stay_at_least": {"target": "spot_t1", "min": 10},
    "before": {"a": "spot_t1", "b": "spot_t2"},
    "same_day": {"a": "spot_t1", "b": "spot_t2"},
    "different_day": {"a": "spot_t1", "b": "spot_t2"},
}

_NAMED_SPOTS = {
    "spot_t1": {"tags_ja": [], "name_ja": "地点イチ"},
    "spot_t2": {"tags_ja": [], "name_ja": "地点ニ"},
}


@pytest.mark.parametrize("pred", sorted(_TARGET_ARGS_BY_PRED))
def test_target_a_b_predicates_never_leak_spot_id_in_message(pred: str) -> None:
    args = _TARGET_ARGS_BY_PRED[pred]
    with predicate_context(_NAMED_SPOTS):
        _, message = PENALTIES[PredEnum(pred)](_itinerary([]), args)

    assert message
    assert not _FORBIDDEN_SPOT_ID_RE.search(message)
