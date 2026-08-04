"""route の正規化と冪等ハッシュを検証する。"""

from app.domains.geo.routes import normalize_route_params, route_params_hash


def test_coordinate_normalization_makes_equivalent_params_idempotent() -> None:
    first = normalize_route_params(
        {"lat": 39.03420004, "lon": 140.03540004},
        {"spot_id": "spot_007"},
        "build-a",
        coordinate_precision=6,
    )
    second = normalize_route_params(
        {"lon": 140.03540003, "lat": 39.03420003},
        {"spot_id": "spot_007"},
        "build-a",
        coordinate_precision=6,
    )

    assert first == second
    assert route_params_hash(first) == route_params_hash(second)


def test_osrm_build_changes_route_hash() -> None:
    first = normalize_route_params(
        {"spot_id": "spot_004"},
        {"spot_id": "spot_007"},
        "build-a",
        coordinate_precision=6,
    )
    second = normalize_route_params(
        {"spot_id": "spot_004"},
        {"spot_id": "spot_007"},
        "build-b",
        coordinate_precision=6,
    )

    assert route_params_hash(first) != route_params_hash(second)
