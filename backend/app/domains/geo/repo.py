"""geo が使う static / app テーブルの読み書き。"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import session_scope
from app.domains.geo.osrm import Coordinate


@dataclass(frozen=True, slots=True)
class SpotRecord:
    spot_id: str
    name_ja: str
    coordinate: Coordinate


@dataclass(frozen=True, slots=True)
class AccessPointRecord:
    access_point_id: str
    name_ja: str | None
    kind: str
    access: str | None
    coordinate: Coordinate


@dataclass(frozen=True, slots=True)
class ApproachRecord:
    spot_id: str
    spot_coordinate: Coordinate
    direct_by_car: bool
    car_node: Coordinate
    access_point_id: str | None
    walk_sec: int
    walk_m: int
    snap_m: int


@dataclass(frozen=True, slots=True)
class TravelTimeRecord:
    from_spot_id: str
    to_spot_id: str
    mode: str
    duration_sec: int
    distance_m: int


@dataclass(frozen=True, slots=True)
class RouteRecord:
    route_id: UUID
    params_hash: str
    params: dict[str, Any]
    mode_summary: str
    distance_m: int
    duration_sec: int
    segments: list[dict[str, Any]]
    geojson: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GeoDataCounts:
    spot_approach: int
    travel_times_car: int
    travel_times_foot: int


class GeoRepository:
    """トランザクション境界から渡された session だけを使用する。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_spots(self) -> list[SpotRecord]:
        result = await self._session.execute(
            text(
                """
                SELECT spot_id,
                       name_ja,
                       ST_X(geom::geometry) AS lon,
                       ST_Y(geom::geometry) AS lat
                  FROM static.spots
                 ORDER BY spot_id
                """
            )
        )
        return [self._spot_from_mapping(row) for row in result.mappings()]

    async def get_spot(self, spot_id: str) -> SpotRecord | None:
        result = await self._session.execute(
            text(
                """
                SELECT spot_id,
                       name_ja,
                       ST_X(geom::geometry) AS lon,
                       ST_Y(geom::geometry) AS lat
                  FROM static.spots
                 WHERE spot_id = :spot_id
                """
            ),
            {"spot_id": spot_id},
        )
        row = result.mappings().one_or_none()
        return self._spot_from_mapping(row) if row is not None else None

    async def list_access_points(self) -> list[AccessPointRecord]:
        result = await self._session.execute(
            text(
                """
                SELECT id,
                       name_ja,
                       kind,
                       access,
                       ST_X(geom::geometry) AS lon,
                       ST_Y(geom::geometry) AS lat
                  FROM static.access_points
                 WHERE access IS DISTINCT FROM 'private'
                 ORDER BY id
                """
            )
        )
        return [
            AccessPointRecord(
                access_point_id=str(row["id"]),
                name_ja=row["name_ja"],
                kind=str(row["kind"]),
                access=row["access"],
                coordinate=Coordinate(lon=float(row["lon"]), lat=float(row["lat"])),
            )
            for row in result.mappings()
        ]

    async def list_approaches(self) -> list[ApproachRecord]:
        result = await self._session.execute(text(self._approach_select_sql("ORDER BY s.spot_id")))
        return [self._approach_from_mapping(row) for row in result.mappings()]

    async def get_approach(self, spot_id: str) -> ApproachRecord | None:
        result = await self._session.execute(
            text(self._approach_select_sql("WHERE s.spot_id = :spot_id")),
            {"spot_id": spot_id},
        )
        row = result.mappings().one_or_none()
        return self._approach_from_mapping(row) if row is not None else None

    async def replace_approaches(self, rows: list[ApproachRecord]) -> None:
        await self._session.execute(text("DELETE FROM static.spot_approach"))
        if not rows:
            return
        await self._session.execute(
            text(
                """
                INSERT INTO static.spot_approach (
                    spot_id, direct_by_car, car_node, access_point_id,
                    walk_sec, walk_m, snap_m
                )
                VALUES (
                    :spot_id,
                    :direct_by_car,
                    ST_SetSRID(ST_MakePoint(:car_lon, :car_lat), 4326)::geography,
                    :access_point_id,
                    :walk_sec,
                    :walk_m,
                    :snap_m
                )
                """
            ),
            [
                {
                    "spot_id": row.spot_id,
                    "direct_by_car": row.direct_by_car,
                    "car_lon": row.car_node.lon,
                    "car_lat": row.car_node.lat,
                    "access_point_id": row.access_point_id,
                    "walk_sec": row.walk_sec,
                    "walk_m": row.walk_m,
                    "snap_m": row.snap_m,
                }
                for row in rows
            ],
        )

    async def travel_time_count(self) -> int:
        value = await self._session.scalar(text("SELECT count(*) FROM static.travel_times"))
        return int(value or 0)

    async def replace_travel_times(self, rows: list[TravelTimeRecord]) -> None:
        await self._session.execute(text("DELETE FROM static.travel_times"))
        if not rows:
            return
        await self._session.execute(
            text(
                """
                INSERT INTO static.travel_times (
                    from_spot_id, to_spot_id, mode, duration_sec, distance_m
                )
                VALUES (
                    :from_spot_id, :to_spot_id, :mode, :duration_sec, :distance_m
                )
                """
            ),
            [
                {
                    "from_spot_id": row.from_spot_id,
                    "to_spot_id": row.to_spot_id,
                    "mode": row.mode,
                    "duration_sec": row.duration_sec,
                    "distance_m": row.distance_m,
                }
                for row in rows
            ],
        )

    async def get_route_by_hash(self, params_hash: str) -> RouteRecord | None:
        result = await self._session.execute(
            text(self._route_select_sql("WHERE params_hash = :params_hash")),
            {"params_hash": params_hash},
        )
        row = result.mappings().one_or_none()
        return self._route_from_mapping(row) if row is not None else None

    async def get_route_by_id(self, route_id: UUID) -> RouteRecord | None:
        result = await self._session.execute(
            text(self._route_select_sql("WHERE id = :route_id")),
            {"route_id": route_id},
        )
        row = result.mappings().one_or_none()
        return self._route_from_mapping(row) if row is not None else None

    async def save_route(
        self,
        *,
        params_hash: str,
        params: dict[str, Any],
        mode_summary: str,
        distance_m: int,
        duration_sec: int,
        segments: list[dict[str, Any]],
        geojson: dict[str, Any],
    ) -> RouteRecord:
        result = await self._session.execute(
            text(
                """
                INSERT INTO app.routes (
                    params_hash, params, mode_summary, distance_m,
                    duration_sec, segments, geojson
                )
                VALUES (
                    :params_hash,
                    CAST(:params AS jsonb),
                    :mode_summary,
                    :distance_m,
                    :duration_sec,
                    CAST(:segments AS jsonb),
                    CAST(:geojson AS jsonb)
                )
                ON CONFLICT (params_hash) DO NOTHING
                RETURNING id, params_hash, params, mode_summary, distance_m,
                          duration_sec, segments, geojson, created_at
                """
            ),
            {
                "params_hash": params_hash,
                "params": json.dumps(params, ensure_ascii=False, separators=(",", ":")),
                "mode_summary": mode_summary,
                "distance_m": distance_m,
                "duration_sec": duration_sec,
                "segments": json.dumps(segments, ensure_ascii=False, separators=(",", ":")),
                "geojson": json.dumps(geojson, ensure_ascii=False, separators=(",", ":")),
            },
        )
        row = result.mappings().one_or_none()
        if row is not None:
            return self._route_from_mapping(row)
        existing = await self.get_route_by_hash(params_hash)
        if existing is None:  # pragma: no cover - unique conflict 後は必ず存在する
            raise RuntimeError("競合した route を再取得できませんでした")
        return existing

    async def geo_data_counts(self) -> GeoDataCounts:
        result = await self._session.execute(
            text(
                """
                SELECT (SELECT count(*) FROM static.spot_approach) AS spot_approach,
                       (SELECT count(*) FROM static.travel_times WHERE mode = 'car')
                           AS travel_times_car,
                       (SELECT count(*) FROM static.travel_times WHERE mode = 'foot')
                           AS travel_times_foot
                """
            )
        )
        row = result.mappings().one()
        return GeoDataCounts(
            spot_approach=int(row["spot_approach"]),
            travel_times_car=int(row["travel_times_car"]),
            travel_times_foot=int(row["travel_times_foot"]),
        )

    @staticmethod
    def _spot_from_mapping(row: Any) -> SpotRecord:
        return SpotRecord(
            spot_id=str(row["spot_id"]),
            name_ja=str(row["name_ja"]),
            coordinate=Coordinate(lon=float(row["lon"]), lat=float(row["lat"])),
        )

    @staticmethod
    def _approach_from_mapping(row: Any) -> ApproachRecord:
        return ApproachRecord(
            spot_id=str(row["spot_id"]),
            spot_coordinate=Coordinate(
                lon=float(row["spot_lon"]),
                lat=float(row["spot_lat"]),
            ),
            direct_by_car=bool(row["direct_by_car"]),
            car_node=Coordinate(lon=float(row["car_lon"]), lat=float(row["car_lat"])),
            access_point_id=row["access_point_id"],
            walk_sec=int(row["walk_sec"]),
            walk_m=int(row["walk_m"]),
            snap_m=int(row["snap_m"]),
        )

    @staticmethod
    def _route_from_mapping(row: Any) -> RouteRecord:
        return RouteRecord(
            route_id=row["id"],
            params_hash=str(row["params_hash"]),
            params=GeoRepository._json_value(row["params"]),
            mode_summary=str(row["mode_summary"]),
            distance_m=int(row["distance_m"]),
            duration_sec=int(row["duration_sec"]),
            segments=GeoRepository._json_value(row["segments"]),
            geojson=GeoRepository._json_value(row["geojson"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @staticmethod
    def _approach_select_sql(suffix: str) -> str:
        return f"""
            SELECT s.spot_id,
                   ST_X(s.geom::geometry) AS spot_lon,
                   ST_Y(s.geom::geometry) AS spot_lat,
                   a.direct_by_car,
                   ST_X(a.car_node::geometry) AS car_lon,
                   ST_Y(a.car_node::geometry) AS car_lat,
                   a.access_point_id,
                   a.walk_sec,
                   a.walk_m,
                   a.snap_m
              FROM static.spots s
              JOIN static.spot_approach a ON a.spot_id = s.spot_id
              {suffix}
        """

    @staticmethod
    def _route_select_sql(suffix: str) -> str:
        return f"""
            SELECT id, params_hash, params, mode_summary, distance_m,
                   duration_sec, segments, geojson, created_at
              FROM app.routes
              {suffix}
        """


async def read_geo_data_counts(settings: Settings | None = None) -> GeoDataCounts:
    """healthz から利用する短い DB 読み取り。"""

    async with session_scope(settings) as session:
        return await GeoRepository(session).geo_data_counts()
