"""レッグ経路の正規化、OSRM 取得、冪等な永続化。"""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.core.config import Settings
from app.domains.geo.osrm import Coordinate, OSRMClient, Profile, RouteResult, read_osrm_build
from app.domains.geo.repo import ApproachRecord, RouteRecord, SpotRecord


class RouteInputError(ValueError):
    """経路端点の指定が契約に合わない。"""


class UnknownSpotError(RouteInputError):
    """指定された spot_id が存在しない。"""


class MissingApproachError(RuntimeError):
    """spot はあるが build-geo が済んでいない。"""


class RouteBuildError(RuntimeError):
    """レッグ全体を制限時間内に構築できない。"""


class RouteRepository(Protocol):
    async def get_route_by_hash(self, params_hash: str) -> RouteRecord | None: ...

    async def get_route_by_id(self, route_id: UUID) -> RouteRecord | None: ...

    async def get_spot(self, spot_id: str) -> SpotRecord | None: ...

    async def get_approach(self, spot_id: str) -> ApproachRecord | None: ...

    async def save_route(self, **values: Any) -> RouteRecord: ...


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    spot_coordinate: Coordinate
    car_node: Coordinate
    needs_walk: bool


@dataclass(frozen=True, slots=True)
class AssembledRoute:
    mode_summary: str
    distance_m: int
    duration_sec: int
    segments: list[dict[str, Any]]
    geojson: dict[str, Any]


def normalize_route_params(
    from_endpoint: Mapping[str, Any],
    to_endpoint: Mapping[str, Any],
    osrm_build: str,
    *,
    coordinate_precision: int,
) -> dict[str, Any]:
    """キー順と座標精度を固定し、同じ入力を同じ JSON にする。"""

    if not osrm_build.strip():
        raise RouteInputError("osrm_build が空です")
    return {
        "from": _normalize_endpoint(from_endpoint, coordinate_precision),
        "to": _normalize_endpoint(to_endpoint, coordinate_precision),
        "osrm_build": osrm_build.strip(),
    }


def route_params_hash(params: Mapping[str, Any]) -> str:
    """正規化済み params の SHA-256 を返す。"""

    canonical = json.dumps(
        params,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assemble_route(
    routed_segments: list[tuple[Profile, RouteResult]],
    *,
    geojson_precision: int,
) -> AssembledRoute:
    """mode ごとの線を FeatureCollection と連結座標インデックスへ変換する。"""

    if not routed_segments:
        raise RouteBuildError("経路セグメントがありません")
    full_coordinates: list[list[float]] = []
    segment_rows: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    modes: set[str] = set()
    for mode, routed in routed_segments:
        coordinates = [
            coordinate.geojson_value(geojson_precision) for coordinate in routed.geometry
        ]
        if len(coordinates) < 2:
            raise RouteBuildError(f"{mode} セグメントの座標が不足しています")
        if full_coordinates and coordinates[0] == full_coordinates[-1]:
            from_index = len(full_coordinates) - 1
            full_coordinates.extend(coordinates[1:])
        else:
            from_index = len(full_coordinates)
            full_coordinates.extend(coordinates)
        to_index = len(full_coordinates) - 1
        distance_m = round(routed.distance_m)
        duration_sec = round(routed.duration_sec)
        segment = {
            "mode": mode,
            "distance_m": distance_m,
            "duration_sec": duration_sec,
            "from_idx": from_index,
            "to_idx": to_index,
        }
        segment_rows.append(segment)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "mode": mode,
                    "from_idx": from_index,
                    "to_idx": to_index,
                },
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
        )
        modes.add(mode)

    if modes == {"car"}:
        mode_summary = "car"
    elif modes == {"foot"}:
        mode_summary = "foot"
    else:
        mode_summary = "car+foot"
    return AssembledRoute(
        mode_summary=mode_summary,
        distance_m=sum(item["distance_m"] for item in segment_rows),
        duration_sec=sum(item["duration_sec"] for item in segment_rows),
        segments=segment_rows,
        geojson={"type": "FeatureCollection", "features": features},
    )


class RouteService:
    """route API と将来の旅程 Tool が共有するレッグ取得サービス。"""

    def __init__(
        self,
        repository: RouteRepository,
        osrm: OSRMClient,
        settings: Settings,
        *,
        osrm_build: str | None = None,
    ) -> None:
        self._repository = repository
        self._osrm = osrm
        self._settings = settings
        self._osrm_build = osrm_build or read_osrm_build()

    async def get_or_create(
        self,
        from_endpoint: Mapping[str, Any],
        to_endpoint: Mapping[str, Any],
    ) -> RouteRecord:
        params = normalize_route_params(
            from_endpoint,
            to_endpoint,
            self._osrm_build,
            coordinate_precision=self._settings.geo_coordinate_precision,
        )
        params_hash = route_params_hash(params)
        existing = await self._repository.get_route_by_hash(params_hash)
        if existing is not None:
            return existing

        source = await self._resolve_endpoint(params["from"])
        target = await self._resolve_endpoint(params["to"])
        requests: list[tuple[Profile, tuple[Coordinate, Coordinate]]] = []
        if source.needs_walk:
            requests.append(("foot", (source.spot_coordinate, source.car_node)))
        requests.append(("car", (source.car_node, target.car_node)))
        if target.needs_walk:
            requests.append(("foot", (target.car_node, target.spot_coordinate)))

        async def fetch_segment(
            mode: Profile,
            coordinates: tuple[Coordinate, Coordinate],
        ) -> tuple[Profile, RouteResult]:
            return mode, await self._osrm.route(mode, list(coordinates))

        try:
            async with asyncio.timeout(self._settings.osrm_leg_timeout_sec):
                routed_segments = await asyncio.gather(
                    *(fetch_segment(mode, coordinates) for mode, coordinates in requests)
                )
        except TimeoutError as exc:
            raise RouteBuildError(
                "レッグ経路が "
                f"{self._settings.osrm_leg_timeout_sec:g} 秒で完了しませんでした"
            ) from exc
        assembled = assemble_route(
            list(routed_segments),
            geojson_precision=self._settings.geo_geojson_precision,
        )
        return await self._repository.save_route(
            params_hash=params_hash,
            params=params,
            mode_summary=assembled.mode_summary,
            distance_m=assembled.distance_m,
            duration_sec=assembled.duration_sec,
            segments=assembled.segments,
            geojson=assembled.geojson,
        )

    async def get(self, route_id: UUID) -> RouteRecord | None:
        return await self._repository.get_route_by_id(route_id)

    async def _resolve_endpoint(self, endpoint: Mapping[str, Any]) -> ResolvedEndpoint:
        spot_id = endpoint.get("spot_id")
        if isinstance(spot_id, str):
            spot = await self._repository.get_spot(spot_id)
            if spot is None:
                raise UnknownSpotError(f"spot_id が見つかりません: {spot_id}")
            approach = await self._repository.get_approach(spot_id)
            if approach is None:
                raise MissingApproachError(
                    "spot_approach がありません。"
                    f"build-geo を実行してください: {spot_id}"
                )
            return ResolvedEndpoint(
                spot_coordinate=spot.coordinate,
                car_node=approach.car_node,
                needs_walk=not approach.direct_by_car,
            )
        coordinate = Coordinate(lon=float(endpoint["lon"]), lat=float(endpoint["lat"]))
        return ResolvedEndpoint(
            spot_coordinate=coordinate,
            car_node=coordinate,
            needs_walk=False,
        )


def _normalize_endpoint(endpoint: Mapping[str, Any], precision: int) -> dict[str, Any]:
    keys = set(endpoint)
    if keys == {"spot_id"}:
        spot_id = endpoint["spot_id"]
        if not isinstance(spot_id, str) or not spot_id.strip():
            raise RouteInputError("spot_id は空でない文字列にしてください")
        return {"spot_id": spot_id.strip()}
    if keys == {"lat", "lon"}:
        lat = _coordinate_number(endpoint["lat"], "lat", -90, 90)
        lon = _coordinate_number(endpoint["lon"], "lon", -180, 180)
        return {
            "lat": _stable_round(lat, precision),
            "lon": _stable_round(lon, precision),
        }
    raise RouteInputError(
        "端点は spot_id または lat/lon のどちらか一方で指定してください"
    )


def _coordinate_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RouteInputError(f"{field} は数値にしてください")
    number = float(value)
    if not minimum <= number <= maximum:
        raise RouteInputError(
            f"{field} は {minimum:g}〜{maximum:g} の範囲にしてください"
        )
    return number


def _stable_round(value: float, precision: int) -> float:
    rounded = round(value, precision)
    return 0.0 if rounded == 0 else rounded
