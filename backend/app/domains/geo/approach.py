"""`static.spot_approach` を OSRM から構築する。"""

import asyncio
import math
from dataclasses import dataclass
from statistics import median

from app.core.config import Settings
from app.domains.geo.osrm import Coordinate, OSRMClient, OSRMNoRouteError, RouteResult
from app.domains.geo.repo import (
    AccessPointRecord,
    ApproachRecord,
    GeoRepository,
    SpotRecord,
)


class ApproachBuildError(RuntimeError):
    """接近情報を正しく決定できない。"""


@dataclass(frozen=True, slots=True)
class ApproachBuildSummary:
    total: int
    direct_by_car: int
    via_access_point: int
    via_road_snap: int
    median_walk_sec: float
    max_walk_sec: int
    walk_over_60_min_count: int


@dataclass(frozen=True, slots=True)
class _ApproachCandidate:
    coordinate: Coordinate
    access_point_id: str | None
    label: str


@dataclass(frozen=True, slots=True)
class _ReachableCandidate:
    candidate: _ApproachCandidate
    route: RouteResult


def straight_line_distance_m(
    first_lon: float,
    first_lat: float,
    second_lon: float,
    second_lat: float,
) -> float:
    """候補の順位付けに十分な haversine 距離を返す。"""

    radius_m = 6_371_008.8
    lat1 = math.radians(first_lat)
    lat2 = math.radians(second_lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second_lon - first_lon)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(value))


def nearest_access_candidates(
    spot: SpotRecord,
    access_points: list[AccessPointRecord],
    limit: int,
) -> list[AccessPointRecord]:
    """private 除外済み候補を直線距離と ID で決定的に並べる。"""

    return sorted(
        access_points,
        key=lambda item: (
            straight_line_distance_m(
                spot.coordinate.lon,
                spot.coordinate.lat,
                item.coordinate.lon,
                item.coordinate.lat,
            ),
            item.access_point_id,
        ),
    )[:limit]


async def select_spot_approach(
    spot: SpotRecord,
    access_points: list[AccessPointRecord],
    osrm: OSRMClient,
    settings: Settings,
) -> ApproachRecord:
    """1地点の車接近可否と、必要なら最短徒歩の接近点を選ぶ。"""

    nearest = await osrm.nearest("car", spot.coordinate)
    if nearest.distance_m <= settings.geo_car_snap_tolerance_m:
        return ApproachRecord(
            spot_id=spot.spot_id,
            spot_coordinate=spot.coordinate,
            direct_by_car=True,
            car_node=spot.coordinate,
            access_point_id=None,
            walk_sec=0,
            walk_m=0,
            snap_m=round(nearest.distance_m),
        )

    access_candidates = nearest_access_candidates(
        spot, access_points, settings.geo_access_candidate_count
    )
    candidates = [
        _ApproachCandidate(
            coordinate=candidate.coordinate,
            access_point_id=candidate.access_point_id,
            label=f"access_point:{candidate.access_point_id}",
        )
        for candidate in access_candidates
    ]
    candidates.append(
        _ApproachCandidate(
            coordinate=nearest.location,
            access_point_id=None,
            label="road_snap",
        )
    )

    async def route_candidate(candidate: _ApproachCandidate) -> _ReachableCandidate | None:
        try:
            route = await osrm.route("foot", [candidate.coordinate, spot.coordinate])
        except OSRMNoRouteError:
            return None
        return _ReachableCandidate(candidate=candidate, route=route)

    routed = await asyncio.gather(*(route_candidate(candidate) for candidate in candidates))
    reachable = [item for item in routed if item is not None]
    if not reachable:
        candidate_labels = [candidate.label for candidate in candidates]
        raise ApproachBuildError(
            f"{spot.spot_id}: 接近候補から徒歩で到達できません: {candidate_labels}"
        )
    selected = min(
        reachable,
        key=lambda item: (
            item.route.duration_sec,
            item.route.distance_m,
            item.candidate.label,
        ),
    )
    if selected.candidate.access_point_id is None:
        snap_m = 0
    else:
        car_snap = await osrm.nearest("car", selected.candidate.coordinate)
        snap_m = round(car_snap.distance_m)
    return ApproachRecord(
        spot_id=spot.spot_id,
        spot_coordinate=spot.coordinate,
        direct_by_car=False,
        car_node=selected.candidate.coordinate,
        access_point_id=selected.candidate.access_point_id,
        walk_sec=round(selected.route.duration_sec),
        walk_m=round(selected.route.distance_m),
        snap_m=snap_m,
    )


async def build_spot_approaches(
    repository: GeoRepository,
    osrm: OSRMClient,
    settings: Settings,
) -> ApproachBuildSummary:
    """全地点を先に計算し、成功した場合だけ43行を置き換える。"""

    spots = await repository.list_spots()
    access_points = await repository.list_access_points()
    if not spots:
        raise ApproachBuildError(
            "static.spots が空です。先に seed を実行してください"
        )
    rows = await asyncio.gather(
        *(select_spot_approach(spot, access_points, osrm, settings) for spot in spots)
    )
    await repository.replace_approaches(list(rows))
    direct_count = sum(row.direct_by_car for row in rows)
    via_access_point_count = sum(row.access_point_id is not None for row in rows)
    walk_seconds = [row.walk_sec for row in rows]
    return ApproachBuildSummary(
        total=len(rows),
        direct_by_car=direct_count,
        via_access_point=via_access_point_count,
        via_road_snap=len(rows) - direct_count - via_access_point_count,
        median_walk_sec=float(median(walk_seconds)),
        max_walk_sec=max(walk_seconds),
        walk_over_60_min_count=sum(value > 60 * 60 for value in walk_seconds),
    )
