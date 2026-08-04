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


@dataclass(frozen=True, slots=True)
class PendingRoute:
    """`get_or_create` の 3 段分割の受け渡し値(2026-08-04 レビュー是正・H-1)。

    段1(`RouteService.lookup_or_prepare`。DB のみ)がキャッシュ miss のときに
    返す。段2(`fetch_route_segments`。OSRM への HTTP 往復。DB session を
    一切握らない)へそのまま渡し、その結果を段3(`RouteService.save`。DB のみ)
    で永続化する。
    """

    params_hash: str
    params: dict[str, Any]
    source: ResolvedEndpoint
    target: ResolvedEndpoint


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


async def fetch_route_segments(
    osrm: OSRMClient,
    pending: PendingRoute,
    *,
    settings: Settings,
) -> AssembledRoute:
    """段2(2026-08-04 レビュー是正・H-1): OSRM への HTTP 往復だけを行う。

    DB session を一切引数に取らない・保持しない。`_FallbackRouteProvider`
    はこの呼び出しの前後で DB session を閉じておくことで、最大 20 秒
    (`osrm_leg_timeout_sec`)かかりうる外部 I/O の間 DB 接続を握り続けない
    ようにする。
    """

    requests: list[tuple[Profile, tuple[Coordinate, Coordinate]]] = []
    if pending.source.needs_walk:
        requests.append(("foot", (pending.source.spot_coordinate, pending.source.car_node)))
    requests.append(("car", (pending.source.car_node, pending.target.car_node)))
    if pending.target.needs_walk:
        requests.append(("foot", (pending.target.car_node, pending.target.spot_coordinate)))

    async def fetch_segment(
        mode: Profile,
        coordinates: tuple[Coordinate, Coordinate],
    ) -> tuple[Profile, RouteResult]:
        return mode, await osrm.route(mode, list(coordinates))

    try:
        async with asyncio.timeout(settings.osrm_leg_timeout_sec):
            routed_segments = await asyncio.gather(
                *(fetch_segment(mode, coordinates) for mode, coordinates in requests)
            )
    except TimeoutError as exc:
        raise RouteBuildError(
            f"レッグ経路が {settings.osrm_leg_timeout_sec:g} 秒で完了しませんでした"
        ) from exc
    return assemble_route(
        list(routed_segments),
        geojson_precision=settings.geo_geojson_precision,
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
        """3 段(照会 → OSRM → 保存)を一括で行う。

        `POST /api/v1/routes` のような単発リクエスト(1 session をリクエスト
        全体で使ってよい文脈)はこちらを使う。ターンの一時 session を
        レッグごとに開閉する `_FallbackRouteProvider`(tool_adapters.py。
        ADR-0020/H-1)は、DB session を OSRM の HTTP 往復中は握らないために
        `lookup_or_prepare` / `fetch_route_segments` / `save` を個別に呼ぶ。
        """

        pending_or_record = await self.lookup_or_prepare(from_endpoint, to_endpoint)
        if not isinstance(pending_or_record, PendingRoute):
            return pending_or_record
        assembled = await fetch_route_segments(
            self._osrm, pending_or_record, settings=self._settings
        )
        return await self.save(pending_or_record, assembled)

    async def lookup_or_prepare(
        self,
        from_endpoint: Mapping[str, Any],
        to_endpoint: Mapping[str, Any],
    ) -> RouteRecord | PendingRoute:
        """段1(H-1): 冪等キャッシュの照会と端点解決(DB のみ・短時間)。

        ヒットすれば `RouteRecord` を返す。miss なら OSRM に渡す材料
        (`PendingRoute`)を返す。
        """

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
        return PendingRoute(params_hash=params_hash, params=params, source=source, target=target)

    async def save(self, pending: PendingRoute, assembled: AssembledRoute) -> RouteRecord:
        """段3(H-1): 永続化だけを行う(DB のみ・短時間)。"""

        return await self._repository.save_route(
            params_hash=pending.params_hash,
            params=pending.params,
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
