"""パック API の 202、冪等性、進捗形、所有者境界。"""

from datetime import UTC, datetime
from uuid import UUID

import httpx

import app.api.routers.packs as packs_router_module
from app.api.auth import get_current_user
from app.api.routers.packs import (
    PackApplicationService,
    PackNotFoundError,
    get_pack_service,
)
from app.api.schemas.packs import (
    PackCreateRequest,
    PackFailure,
    PackJobResponse,
    PackProgress,
    PackResponse,
)
from app.core.config import Settings
from app.domains.packs.planner import PackJobData
from app.domains.users import UserData
from app.main import create_app

JOB_ID = UUID("00000000-0000-0000-0000-000000000010")
PACK_ID = UUID("00000000-0000-0000-0000-000000000020")


class MemoryPackService:
    def __init__(self) -> None:
        self.create_calls = 0

    async def create(self, *, user_id: int, request: PackCreateRequest) -> PackJobData:
        assert user_id == 1
        assert request.itinerary_version == 4
        self.create_calls += 1
        return PackJobData(
            job_id=JOB_ID,
            pack_id=PACK_ID,
            user_id=1,
            itinerary_version=4,
            epoch=0,
            state="queued",
            params={},
            params_hash="same-key",
            progress={"done": 0, "total": 11, "failed": 0},
        )

    async def job(self, *, user_id: int, job_id: UUID) -> PackJobResponse:
        if user_id != 1 or job_id != JOB_ID:
            raise PackNotFoundError
        return PackJobResponse(
            state="running",
            progress=PackProgress(done=3, total=11, failed=1),
            pack_id=PACK_ID,
            failures=[
                PackFailure(
                    spot_id="spot_001",
                    variant="weather_rain",
                    reason="tts_failed",
                )
            ],
        )

    async def pack(self, *, user_id: int, pack_id: UUID) -> PackResponse:
        if user_id != 1 or pack_id != PACK_ID:
            raise PackNotFoundError
        return PackResponse(
            state="partial",
            itinerary_version=4,
            manifest_url=f"/packs/{PACK_ID}/manifest.json",
            total_bytes=1234,
            missing=[],
        )


async def test_create_commits_job_before_202_can_be_sent(monkeypatch, tmp_path) -> None:
    expected = PackJobData(
        job_id=JOB_ID,
        pack_id=PACK_ID,
        user_id=1,
        itinerary_version=4,
        epoch=0,
        state="queued",
        params={},
        params_hash="same-key",
        progress={"done": 0, "total": 11, "failed": 0},
    )

    class FakeSession:
        commits = 0

        async def commit(self) -> None:
            self.commits += 1

    class FakePlanner:
        def __init__(self, session, settings) -> None:
            del session, settings

        async def request_pack(self, **kwargs) -> PackJobData:
            assert kwargs["user_id"] == 1
            return expected

    monkeypatch.setattr(packs_router_module, "PackPlanner", FakePlanner)
    session = FakeSession()
    service = PackApplicationService(
        session,
        Settings(_env_file=None, POSTGRES_PASSWORD="test", PACKS_ROOT=tmp_path),
    )

    result = await service.create(
        user_id=1,
        request=PackCreateRequest(itinerary_version=4),
    )

    assert result == expected
    assert session.commits == 1


async def test_pack_endpoints_are_idempotent_and_hide_other_users() -> None:
    service = MemoryPackService()
    now = datetime.now(UTC)
    current_user = UserData(1, "pack-owner", "token", None, now, now)
    app = create_app()
    app.dependency_overrides[get_pack_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: current_user

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/api/v1/packs", json={"itinerary_version": 4})
        second = await client.post("/api/v1/packs", json={"itinerary_version": 4})
        progress = await client.get(f"/api/v1/jobs/{JOB_ID}")
        metadata = await client.get(f"/api/v1/packs/{PACK_ID}")
        current_user = UserData(2, "other", "other-token", None, now, now)
        hidden_job = await client.get(f"/api/v1/jobs/{JOB_ID}")
        hidden_pack = await client.get(f"/api/v1/packs/{PACK_ID}")

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() == {
        "job_id": str(JOB_ID),
        "pack_id": str(PACK_ID),
        "state": "queued",
        "total": 11,
    }
    assert service.create_calls == 2
    assert progress.status_code == 200
    assert progress.json()["progress"] == {"done": 3, "total": 11, "failed": 1}
    assert metadata.status_code == 200
    assert metadata.json()["manifest_url"].endswith("/manifest.json")
    assert hidden_job.status_code == hidden_pack.status_code == 404
