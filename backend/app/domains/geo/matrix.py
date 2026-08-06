"""door-to-door の `static.travel_times` 行列を構築する。"""

import asyncio
from dataclasses import dataclass

from app.core.config import Settings
from app.domains.geo.osrm import OSRMClient, TableResult
from app.domains.geo.repo import (
    ApproachRecord,
    GeoRepository,
    SpotRecord,
    TravelTimeRecord,
)


class MatrixBuildError(RuntimeError):
    """移動時間行列を完全かつ正しく構築できない。"""


class MissingCarRoutesError(MatrixBuildError):
    """car 行列に OSRM の null が含まれる。"""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self.pairs = pairs
        pair_text = ", ".join(f"{source}->{target}" for source, target in pairs)
        super().__init__(f"car 行列に欠損があります ({len(pairs)}件): {pair_text}")


@dataclass(frozen=True, slots=True)
class MatrixBuildResult:
    skipped: bool
    car_rows: int
    foot_rows: int


def build_car_rows(
    spots: list[SpotRecord],
    approaches: dict[str, ApproachRecord],
    table: TableResult,
) -> list[TravelTimeRecord]:
    """OSRM の車区間に両端の徒歩時間・距離を加える。"""

    _validate_table_shape(table, len(spots), "car")
    missing_pairs: list[tuple[str, str]] = []
    rows: list[TravelTimeRecord] = []
    for source_index, source in enumerate(spots):
        source_approach = approaches[source.spot_id]
        for target_index, target in enumerate(spots):
            if source_index == target_index:
                continue
            duration = table.durations[source_index][target_index]
            distance = table.distances[source_index][target_index]
            if duration is None or distance is None:
                missing_pairs.append((source.spot_id, target.spot_id))
                continue
            target_approach = approaches[target.spot_id]
            rows.append(
                TravelTimeRecord(
                    from_spot_id=source.spot_id,
                    to_spot_id=target.spot_id,
                    mode="car",
                    duration_sec=round(
                        source_approach.walk_sec + duration + target_approach.walk_sec
                    ),
                    distance_m=round(
                        source_approach.walk_m + distance + target_approach.walk_m
                    ),
                )
            )
    if missing_pairs:
        raise MissingCarRoutesError(missing_pairs)
    return rows


def build_foot_rows(
    spots: list[SpotRecord],
    table: TableResult,
    *,
    max_duration_sec: int,
    max_distance_m: int,
) -> list[TravelTimeRecord]:
    """徒歩として現実的な近接ペアだけを残す。"""

    _validate_table_shape(table, len(spots), "foot")
    rows: list[TravelTimeRecord] = []
    for source_index, source in enumerate(spots):
        for target_index, target in enumerate(spots):
            if source_index == target_index:
                continue
            duration = table.durations[source_index][target_index]
            distance = table.distances[source_index][target_index]
            if duration is None or distance is None:
                continue
            if duration <= max_duration_sec and distance <= max_distance_m:
                rows.append(
                    TravelTimeRecord(
                        from_spot_id=source.spot_id,
                        to_spot_id=target.spot_id,
                        mode="foot",
                        duration_sec=round(duration),
                        distance_m=round(distance),
                    )
                )
    return rows


async def build_travel_time_matrix(
    repository: GeoRepository,
    osrm: OSRMClient,
    settings: Settings,
    *,
    force: bool = False,
) -> MatrixBuildResult:
    """2回の table 結果を検証し、1トランザクションで置き換える。"""

    if not force and await repository.travel_time_count() > 0:
        counts = await repository.geo_data_counts()
        return MatrixBuildResult(
            skipped=True,
            car_rows=counts.travel_times_car,
            foot_rows=counts.travel_times_foot,
        )

    spots = await repository.list_spots()
    approaches = await repository.list_approaches()
    if not spots:
        raise MatrixBuildError(
            "static.spots が空です。先に seed を実行してください"
        )
    approach_by_spot = {row.spot_id: row for row in approaches}
    missing_approaches = [spot.spot_id for spot in spots if spot.spot_id not in approach_by_spot]
    if missing_approaches:
        raise MatrixBuildError(
            "spot_approach が不足しています。"
            "先に build-geo を実行してください: "
            f"{missing_approaches}"
        )

    car_coordinates = [approach_by_spot[spot.spot_id].car_node for spot in spots]
    foot_coordinates = [spot.coordinate for spot in spots]
    car_table, foot_table = await asyncio.gather(
        osrm.table("car", car_coordinates),
        osrm.table("foot", foot_coordinates),
    )
    car_rows = build_car_rows(spots, approach_by_spot, car_table)
    foot_rows = build_foot_rows(
        spots,
        foot_table,
        max_duration_sec=settings.geo_foot_max_duration_sec,
        max_distance_m=settings.geo_foot_max_distance_m,
    )
    await repository.replace_travel_times([*car_rows, *foot_rows])
    return MatrixBuildResult(
        skipped=False,
        car_rows=len(car_rows),
        foot_rows=len(foot_rows),
    )


def _validate_table_shape(table: TableResult, size: int, mode: str) -> None:
    matrices = {"durations": table.durations, "distances": table.distances}
    for name, matrix in matrices.items():
        if len(matrix) != size or any(len(row) != size for row in matrix):
            raise MatrixBuildError(
                f"{mode} {name} の形が {size}x{size} ではありません"
            )
