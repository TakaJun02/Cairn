"""compose PostgreSQL 上で沿道 POI の PostGIS クエリを検証する。"""

import asyncio
import os

import pytest

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.domains.geo.along import find_along_pois
from app.domains.geo.repo import GeoRepository

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


def test_find_along_pois_uses_postgis_route_position() -> None:
    settings = get_settings()

    async def exercise() -> None:
        async with session_scope(settings) as session:
            spots = await GeoRepository(session).list_spots()
            assert len(spots) >= 2
            source, target = spots[:2]
            line_coordinates = [
                source.coordinate.geojson_value(6),
                target.coordinate.geojson_value(6),
            ]
            results = await find_along_pois(
                session,
                mode_geom={"type": "MultiLineString", "coordinates": [line_coordinates]},
                route_line={"type": "LineString", "coordinates": line_coordinates},
                buffer_m=1.0,
                exclude_spot_ids=[source.spot_id],
            )
        assert any(item.spot_id == target.spot_id for item in results)
        target_result = next(item for item in results if item.spot_id == target.spot_id)
        assert target_result.distance_m < 1.0
        assert target_result.route_position == pytest.approx(1.0)
        await dispose_engine()

    asyncio.run(exercise())
