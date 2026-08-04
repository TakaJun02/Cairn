"""door-to-door 合成と foot 閾値を純粋関数で検証する。"""

from app.domains.geo.matrix import build_car_rows, build_foot_rows
from app.domains.geo.osrm import Coordinate, TableResult
from app.domains.geo.repo import ApproachRecord, SpotRecord


def _spots() -> list[SpotRecord]:
    return [
        SpotRecord("spot_a", "A", Coordinate(140.0, 39.0)),
        SpotRecord("spot_b", "B", Coordinate(140.1, 39.1)),
    ]


def test_car_rows_add_walk_at_both_ends() -> None:
    spots = _spots()
    approaches = {
        "spot_a": ApproachRecord(
            "spot_a", spots[0].coordinate, False, Coordinate(140.01, 39.01), "ap_a", 60, 100, 3
        ),
        "spot_b": ApproachRecord(
            "spot_b", spots[1].coordinate, False, Coordinate(140.09, 39.09), "ap_b", 120, 200, 4
        ),
    }
    table = TableResult(
        durations=((0.0, 100.0), (110.0, 0.0)),
        distances=((0.0, 1000.0), (1050.0, 0.0)),
    )

    rows = build_car_rows(spots, approaches, table)

    forward = next(row for row in rows if row.from_spot_id == "spot_a")
    reverse = next(row for row in rows if row.from_spot_id == "spot_b")
    assert forward.duration_sec == 60 + 100 + 120
    assert forward.distance_m == 100 + 1000 + 200
    assert reverse.duration_sec == 120 + 110 + 60


def test_foot_rows_require_both_duration_and_distance_thresholds() -> None:
    spots = [
        *_spots(),
        SpotRecord("spot_c", "C", Coordinate(140.2, 39.2)),
    ]
    table = TableResult(
        durations=(
            (0.0, 1800.0, 1700.0),
            (1801.0, 0.0, 100.0),
            (100.0, 100.0, 0.0),
        ),
        distances=(
            (0.0, 2500.0, 2600.0),
            (1000.0, 0.0, 2501.0),
            (100.0, 200.0, 0.0),
        ),
    )

    rows = build_foot_rows(
        spots,
        table,
        max_duration_sec=1800,
        max_distance_m=2500,
    )

    pairs = {(row.from_spot_id, row.to_spot_id) for row in rows}
    assert ("spot_a", "spot_b") in pairs
    assert ("spot_a", "spot_c") not in pairs
    assert ("spot_b", "spot_a") not in pairs
    assert ("spot_b", "spot_c") not in pairs
    assert ("spot_c", "spot_a") in pairs
    assert ("spot_c", "spot_b") in pairs
