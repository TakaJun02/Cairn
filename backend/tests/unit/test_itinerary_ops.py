"""8 種の編集 op と diff の純粋な意味論を検証する。"""

import pytest

from app.domains.itinerary.ops import OpApplicationError, apply_ops, calculate_diff, find_revert
from app.domains.itinerary.types import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    LegFromPrev,
    SpotEndpoint,
)

KNOWN = {"spot_a", "spot_b", "spot_c", "spot_d", "spot_e"}
STAYS = {spot_id: 30 for spot_id in KNOWN}


def _item(spot_id: str, seq: int, arrive: int, *, locked: bool = False) -> ItineraryItem:
    return ItineraryItem(
        seq=seq,
        spot_id=spot_id,
        arrive_min=arrive,
        stay_min=30,
        depart_min=arrive + 30,
        leg_from_prev=LegFromPrev(mode="car", min=10),
        locked=locked,
    )


def _itinerary() -> Itinerary:
    endpoint = SpotEndpoint(spot_id="spot_e")
    return Itinerary(
        days=[
            ItineraryDay(
                date="2026-08-10",
                start_min=540,
                end_min=1020,
                origin=endpoint,
                destination=endpoint,
                items=[_item("spot_a", 1, 550), _item("spot_b", 2, 590)],
            ),
            ItineraryDay(
                date="2026-08-11",
                start_min=540,
                end_min=1020,
                origin=endpoint,
                destination=endpoint,
                items=[_item("spot_c", 1, 550)],
            ),
        ],
        version=3,
    )


def _ids(itinerary: Itinerary) -> list[list[str]]:
    return [[item.spot_id for item in day.items] for day in itinerary.days]


def test_add_remove_move_replace_lock_stay_and_time_apply_in_order() -> None:
    original = _itinerary()
    result = apply_ops(
        original,
        [
            {"op": "add", "targets": ["spot_d"], "day": 1, "after": "spot_a"},
            {"op": "remove", "targets": ["spot_b"]},
            {"op": "move", "target": "spot_c", "day": 1, "position": 1},
            {"op": "replace", "target": "spot_a", "with": "spot_b"},
            {"op": "lock", "targets": ["spot_b"], "locked": True},
            {"op": "set_stay", "target": "spot_d", "min": 45},
            {"op": "set_time", "target": "spot_d", "arrive": "13:10", "depart": "14:00"},
        ],
        known_spot_ids=KNOWN,
        default_stays=STAYS,
    )

    assert _ids(original) == [["spot_a", "spot_b"], ["spot_c"]]
    assert _ids(result.itinerary) == [["spot_c", "spot_b", "spot_d"], []]
    items = {item.spot_id: item for day in result.itinerary.days for item in day.items}
    assert items["spot_b"].locked is True
    assert items["spot_d"].arrive_min == 790
    assert items["spot_d"].depart_min == 840
    assert items["spot_d"].stay_min == 50
    assert result.excluded_spot_ids == {"spot_a"}
    assert result.required_spot_ids == {"spot_b", "spot_d"}
    assert result.preferred_windows["spot_d"] == (790, 840)
    assert [item.seq for item in result.itinerary.days[0].items] == [1, 2, 3]


def test_move_without_day_keeps_the_source_day() -> None:
    result = apply_ops(
        _itinerary(),
        [{"op": "move", "target": "spot_c", "position": 1}],
        known_spot_ids=KNOWN,
        default_stays=STAYS,
    )

    assert _ids(result.itinerary) == [["spot_a", "spot_b"], ["spot_c"]]


def test_revert_wins_over_mixed_ops_before_other_references_are_parsed() -> None:
    revert = find_revert(
        [
            {"op": "add", "targets": "$99.spot_ids"},
            {"op": "revert", "to_version": 2},
            {"op": "remove", "targets": ["spot_missing"]},
        ]
    )

    assert revert is not None
    assert revert.to_version == 2


def test_apply_ops_rejects_unresolved_or_missing_references() -> None:
    with pytest.raises(OpApplicationError, match="解決されていません"):
        apply_ops(
            _itinerary(),
            [{"op": "add", "targets": "$1.spot_ids"}],
            known_spot_ids=KNOWN,
            default_stays=STAYS,
        )
    with pytest.raises(OpApplicationError, match="含まれない"):
        apply_ops(
            _itinerary(),
            [{"op": "remove", "targets": ["spot_d"]}],
            known_spot_ids=KNOWN,
            default_stays=STAYS,
        )


def test_diff_reports_added_removed_moved_and_retimed() -> None:
    previous = _itinerary()
    current = apply_ops(
        previous,
        [
            {"op": "remove", "targets": ["spot_b"]},
            {"op": "add", "targets": ["spot_d"], "day": 1},
            {"op": "move", "target": "spot_c", "day": 1, "position": 1},
            {"op": "set_stay", "target": "spot_a", "min": 45},
        ],
        known_spot_ids=KNOWN,
        default_stays=STAYS,
    ).itinerary

    diff = calculate_diff(previous, current)

    assert diff.added == ["spot_d"]
    assert diff.removed == ["spot_b"]
    assert {item.spot_id for item in diff.moved} == {"spot_a", "spot_c"}
    assert diff.retimed == ["spot_a"]
