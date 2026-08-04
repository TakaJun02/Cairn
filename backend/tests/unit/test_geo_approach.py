"""spot_approach の候補選定をネットワークなしで検証する。"""

from app.core.config import Settings
from app.domains.geo.approach import select_spot_approach
from app.domains.geo.osrm import Coordinate, NearestResult, RouteResult
from app.domains.geo.repo import AccessPointRecord, SpotRecord


class FakeOSRM:
    def __init__(
        self,
        *,
        road_snap: Coordinate,
        road_snap_distance_m: float,
        routes: dict[Coordinate, tuple[float, float]],
        access_snap_distance_m: float = 4.0,
    ) -> None:
        self.road_snap = road_snap
        self.road_snap_distance_m = road_snap_distance_m
        self.routes = routes
        self.access_snap_distance_m = access_snap_distance_m
        self.nearest_calls: list[Coordinate] = []
        self.route_calls: list[tuple[Coordinate, Coordinate]] = []

    async def nearest(self, profile, coordinate):
        assert profile == "car"
        self.nearest_calls.append(coordinate)
        if len(self.nearest_calls) == 1:
            return NearestResult(
                location=self.road_snap,
                distance_m=self.road_snap_distance_m,
            )
        return NearestResult(
            location=coordinate,
            distance_m=self.access_snap_distance_m,
        )

    async def route(self, profile, coordinates):
        assert profile == "foot"
        start, end = coordinates
        self.route_calls.append((start, end))
        distance, duration = self.routes[start]
        return RouteResult(
            distance_m=distance,
            duration_sec=duration,
            geometry=(start, end),
        )


async def test_select_spot_approach_uses_fastest_of_nearest_candidates() -> None:
    settings = Settings(
        _env_file=None,
        GEO_ACCESS_CANDIDATE_COUNT=2,
        GEO_CAR_SNAP_TOLERANCE_M=50,
    )
    spot = SpotRecord("spot_test", "テスト地点", Coordinate(140.0, 39.0))
    access_points = [
        AccessPointRecord("ap_slow", None, "parking", None, Coordinate(140.001, 39.0)),
        AccessPointRecord("ap_fast", None, "parking", None, Coordinate(140.002, 39.0)),
        AccessPointRecord("ap_far", None, "parking", None, Coordinate(141.0, 40.0)),
    ]
    road_snap = Coordinate(140.0005, 39.0)
    osrm = FakeOSRM(
        road_snap=road_snap,
        road_snap_distance_m=120.0,
        routes={
            access_points[0].coordinate: (600.0, 600.0),
            access_points[1].coordinate: (240.0, 240.0),
            road_snap: (300.0, 300.0),
        },
    )

    result = await select_spot_approach(spot, access_points, osrm, settings)

    assert result.direct_by_car is False
    assert result.access_point_id == "ap_fast"
    assert result.car_node == Coordinate(140.002, 39.0)
    assert result.walk_sec == 240
    assert result.walk_m == 240
    assert result.snap_m == 4
    assert osrm.nearest_calls == [spot.coordinate, access_points[1].coordinate]
    assert {start for start, _ in osrm.route_calls} == {
        access_points[0].coordinate,
        access_points[1].coordinate,
        road_snap,
    }


async def test_select_spot_approach_uses_road_snap_when_access_points_are_far() -> None:
    settings = Settings(
        _env_file=None,
        GEO_ACCESS_CANDIDATE_COUNT=2,
        GEO_CAR_SNAP_TOLERANCE_M=50,
    )
    spot = SpotRecord("spot_test", "テスト地点", Coordinate(140.0, 39.0))
    access_points = [
        AccessPointRecord("ap_far_1", None, "parking", None, Coordinate(140.5, 39.0)),
        AccessPointRecord("ap_far_2", None, "parking", None, Coordinate(141.0, 39.0)),
    ]
    road_snap = Coordinate(140.001, 39.0)
    osrm = FakeOSRM(
        road_snap=road_snap,
        road_snap_distance_m=119.1,
        routes={
            access_points[0].coordinate: (30_000.0, 21_600.0),
            access_points[1].coordinate: (40_000.0, 28_800.0),
            road_snap: (119.0, 120.0),
        },
    )

    result = await select_spot_approach(spot, access_points, osrm, settings)

    assert result.direct_by_car is False
    assert result.access_point_id is None
    assert result.car_node == road_snap
    assert result.walk_sec == 120
    assert result.walk_m == 119
    assert result.snap_m == 0
    assert osrm.nearest_calls == [spot.coordinate]
