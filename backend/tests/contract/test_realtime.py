"""realtime ETag、手動設定、simulator 管理 API の契約。"""

from datetime import UTC, datetime

import httpx

from app.api.auth import get_current_user
from app.api.routers.realtime import get_realtime_store, get_simulator_service
from app.domains.realtime.simulator import SimulatorStateData
from app.domains.realtime.store import RealtimeSpotData, RealtimeSpotNotFoundError
from app.domains.users import UserData
from app.main import create_app


class MemoryRealtimeStore:
    def __init__(self) -> None:
        self.value = RealtimeSpotData("spot_007", None, None, None, None)

    async def get(self, spot_id: str) -> RealtimeSpotData:
        if spot_id != "spot_007":
            raise RealtimeSpotNotFoundError(spot_id)
        return self.value

    async def set(
        self,
        spot_id: str,
        *,
        weather: int | None,
        congestion: int | None,
        source: str,
    ) -> RealtimeSpotData:
        if spot_id != "spot_007":
            raise RealtimeSpotNotFoundError(spot_id)
        self.value = RealtimeSpotData(
            spot_id,
            weather,
            congestion,
            source,
            datetime(2026, 8, 2, 0, tzinfo=UTC),
        )
        return self.value


def _state(*, running: bool) -> SimulatorStateData:
    return SimulatorStateData(
        loaded=True,
        name="rainy_afternoon",
        running=running,
        speed=60,
        elapsed_min=0,
        next_event_index=0,
        event_count=1,
        virtual_time=datetime(2026, 8, 10, 9, tzinfo=UTC),
    )


class MemorySimulatorService:
    async def load(self, scenario) -> SimulatorStateData:
        assert scenario.name == "rainy_afternoon"
        return _state(running=False)

    async def start(self, *, speed: float) -> SimulatorStateData:
        assert speed == 60
        return _state(running=True)

    async def stop(self) -> SimulatorStateData:
        return _state(running=False)


async def test_realtime_spot_etag_304_and_manual_update() -> None:
    store = MemoryRealtimeStore()
    now = datetime.now(UTC)
    current_user = UserData(1, "p01", "token", None, now, now)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_realtime_store] = lambda: store

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/realtime/spots/spot_007")
        cached = await client.get(
            "/api/v1/realtime/spots/spot_007",
            headers={"If-None-Match": first.headers["etag"]},
        )
        updated = await client.post(
            "/api/v1/realtime/spots/spot_007",
            json={"weather": 2, "congestion": 1},
        )

    assert first.status_code == 200
    assert first.json()["weather"] is None
    assert first.json()["congestion"] is None
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == first.headers["etag"]
    assert updated.status_code == 200
    assert updated.json()["source"] == "simulated"
    assert updated.headers["etag"] != first.headers["etag"]


async def test_simulator_management_actions_share_service() -> None:
    service = MemorySimulatorService()
    now = datetime.now(UTC)
    current_user = UserData(1, "p01", "token", None, now, now)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_simulator_service] = lambda: service
    scenario = {
        "name": "rainy_afternoon",
        "base_time": "2026-08-10T09:00:00+09:00",
        "events": [
            {
                "at_min": 0,
                "spot_id": "spot_007",
                "weather": 0,
                "congestion": 1,
            }
        ],
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        loaded = await client.post(
            "/api/v1/realtime/simulator/load",
            json={"scenario": scenario},
        )
        started = await client.post(
            "/api/v1/realtime/simulator/start",
            json={"speed": 60},
        )
        stopped = await client.post("/api/v1/realtime/simulator/stop")
        invalid = await client.post("/api/v1/realtime/simulator/unknown")

    assert loaded.status_code == 200
    assert loaded.json()["loaded"] is True
    assert started.status_code == 200
    assert started.json()["running"] is True
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert invalid.status_code == 404
