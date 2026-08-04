"""外部 LLM/TTS を使わず、202 から ready までのポーリング導線を確認する。"""

from datetime import UTC, datetime
from uuid import UUID

import httpx

from app.api.auth import get_current_user
from app.api.routers.packs import get_pack_service
from app.api.schemas.packs import PackCreateRequest, PackJobResponse, PackProgress, PackResponse
from app.domains.packs.planner import PackJobData
from app.domains.users import UserData
from app.main import create_app

JOB_ID = UUID("00000000-0000-0000-0000-000000000030")
PACK_ID = UUID("00000000-0000-0000-0000-000000000040")


class PollingPackService:
    def __init__(self) -> None:
        self.polls = 0

    async def create(self, *, user_id: int, request: PackCreateRequest) -> PackJobData:
        return PackJobData(
            JOB_ID,
            PACK_ID,
            user_id,
            request.itinerary_version,
            0,
            "queued",
            {},
            "hash",
            {"done": 0, "total": 5, "failed": 0},
        )

    async def job(self, *, user_id: int, job_id: UUID) -> PackJobResponse:
        del user_id, job_id
        self.polls += 1
        ready = self.polls >= 2
        return PackJobResponse(
            state="ready" if ready else "running",
            progress=PackProgress(done=5 if ready else 2, total=5, failed=0),
            pack_id=PACK_ID,
            failures=[],
        )

    async def pack(self, *, user_id: int, pack_id: UUID) -> PackResponse:
        del user_id, pack_id
        return PackResponse(
            state="ready",
            itinerary_version=1,
            manifest_url=f"/packs/{PACK_ID}/manifest.json",
            total_bytes=100,
            missing=[],
        )


async def test_post_then_poll_reaches_ready_without_blocking_post() -> None:
    service = PollingPackService()
    now = datetime.now(UTC)
    app = create_app()
    app.dependency_overrides[get_pack_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: UserData(
        1, "smoke", "token", None, now, now
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post("/api/v1/packs", json={"itinerary_version": 1})
        first = await client.get(f"/api/v1/jobs/{JOB_ID}")
        second = await client.get(f"/api/v1/jobs/{JOB_ID}")

    assert created.status_code == 202
    assert first.json()["state"] == "running"
    assert second.json()["state"] == "ready"
    assert second.json()["progress"] == {"done": 5, "total": 5, "failed": 0}
