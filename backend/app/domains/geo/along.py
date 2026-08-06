"""PostGIS だけで沿道 POI の距離と行程上の位置を求める。"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class AlongPoi:
    spot_id: str
    name_ja: str
    distance_m: float
    route_position: float


def buffer_for_mode(settings: Settings, mode: Literal["car", "foot"]) -> float:
    """調整値の参照点を Settings に限定する。"""

    if mode == "car":
        return settings.geo_along_car_buffer_m
    if mode == "foot":
        return settings.geo_along_foot_buffer_m
    raise ValueError(f"未対応の mode です: {mode}")


async def find_along_pois(
    session: AsyncSession,
    *,
    mode_geom: dict[str, Any],
    route_line: dict[str, Any],
    buffer_m: float,
    exclude_spot_ids: list[str],
) -> list[AlongPoi]:
    """mode 1つにつき SQL 1本で沿道地点を移動順に返す。"""

    if mode_geom.get("type") != "MultiLineString":
        raise ValueError("mode_geom は MultiLineString にしてください")
    if route_line.get("type") != "LineString":
        raise ValueError("route_line は LineString にしてください")
    if buffer_m <= 0:
        raise ValueError("buffer_m は正数にしてください")
    result = await session.execute(
        text(
            """
            WITH m AS (
                   SELECT ST_GeomFromGeoJSON(CAST(:mode_geom AS text)) AS g
                 ),
                 l AS (
                   SELECT ST_GeomFromGeoJSON(CAST(:route_line AS text)) AS g
                 )
            SELECT s.spot_id,
                   s.name_ja,
                   ST_Distance(s.geom, (SELECT g FROM m)::geography) AS distance_m,
                   ST_LineLocatePoint((SELECT g FROM l), s.geom::geometry) AS route_position
              FROM static.spots s
             WHERE ST_DWithin(s.geom, (SELECT g FROM m)::geography, :buffer_m)
               AND NOT (s.spot_id = ANY(CAST(:exclude_spot_ids AS text[])))
             ORDER BY route_position
            """
        ),
        {
            "mode_geom": json.dumps(mode_geom, ensure_ascii=False, separators=(",", ":")),
            "route_line": json.dumps(route_line, ensure_ascii=False, separators=(",", ":")),
            "buffer_m": buffer_m,
            "exclude_spot_ids": exclude_spot_ids,
        },
    )
    return [
        AlongPoi(
            spot_id=str(row["spot_id"]),
            name_ja=str(row["name_ja"]),
            distance_m=float(row["distance_m"]),
            route_position=float(row["route_position"]),
        )
        for row in result.mappings()
    ]


def merge_along_pois(*groups: list[AlongPoi]) -> list[AlongPoi]:
    """mode 間で重複した地点は、経路への距離が短い結果を残す。"""

    selected: dict[str, AlongPoi] = {}
    for group in groups:
        for item in group:
            current = selected.get(item.spot_id)
            if current is None or item.distance_m < current.distance_m:
                selected[item.spot_id] = item
    return sorted(selected.values(), key=lambda item: item.route_position)
