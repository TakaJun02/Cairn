"""routes REST 契約を respx の OSRM 応答で検証する。"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import respx

from app.api.routers.routes import get_geo_repository
from app.core.config import Settings, get_settings
from app.domains.geo.osrm import Coordinate
from app.domains.geo.repo import ApproachRecord, RouteRecord, SpotRecord
from app.main import create_app


class MemoryRouteRepository:
    def __init__(self) -> None:
        self.by_hash: dict[str, RouteRecord] = {}
        self.by_id: dict[UUID, RouteRecord] = {}
        self.spots = {
            "spot_004": SpotRecord("spot_004", "出発地", Coordinate(140.0, 39.0)),
            "spot_007": SpotRecord("spot_007", "到着地", Coordinate(140.1, 39.1)),
        }
        self.approaches = {
            spot_id: ApproachRecord(
                spot_id,
                spot.coordinate,
                True,
                spot.coordinate,
                None,
                0,
                0,
                1,
            )
            for spot_id, spot in self.spots.items()
        }

    async def get_route_by_hash(self, params_hash: str) -> RouteRecord | None:
        return self.by_hash.get(params_hash)

    async def get_route_by_id(self, route_id: UUID) -> RouteRecord | None:
        return self.by_id.get(route_id)

    async def get_spot(self, spot_id: str) -> SpotRecord | None:
        return self.spots.get(spot_id)

    async def get_approach(self, spot_id: str) -> ApproachRecord | None:
        return self.approaches.get(spot_id)

    async def save_route(self, **values: Any) -> RouteRecord:
        existing = self.by_hash.get(values["params_hash"])
        if existing is not None:
            return existing
        record = RouteRecord(
            route_id=uuid4(),
            params_hash=values["params_hash"],
            params=values["params"],
            mode_summary=values["mode_summary"],
            distance_m=values["distance_m"],
            duration_sec=values["duration_sec"],
            segments=values["segments"],
            geojson=values["geojson"],
            created_at=datetime.now(UTC),
        )
        self.by_hash[record.params_hash] = record
        self.by_id[record.route_id] = record
        return record


@respx.mock
async def test_post_routes_is_idempotent_and_get_returns_same_route() -> None:
    repository = MemoryRouteRepository()
    settings = Settings(
        _env_file=None,
        POSTGRES_PASSWORD="test",
        OSRM_CAR_URL="http://osrm-car.test",
        OSRM_FOOT_URL="http://osrm-foot.test",
    )
    route_mock = respx.get(url__regex=r"http://osrm-car\.test/route/v1/car/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [
                    {
                        "distance": 1234.4,
                        "duration": 345.6,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[140.0, 39.0], [140.1, 39.1]],
                        },
                    }
                ],
            },
        )
    )

    app = create_app()
    app.dependency_overrides[get_geo_repository] = lambda: repository
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        payload = {"from": {"spot_id": "spot_004"}, "to": {"spot_id": "spot_007"}}
        first = await client.post("/api/v1/routes", json=payload)
        second = await client.post("/api/v1/routes", json=payload)
        route_id = first.json()["route_id"]
        fetched = await client.get(f"/api/v1/routes/{route_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert fetched.status_code == 200
    assert first.json() == second.json() == fetched.json()
    assert first.json()["mode_summary"] == "car"
    assert route_mock.call_count == 1
