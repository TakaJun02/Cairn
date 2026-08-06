"""spots の全件応答と ETag 再検証を検証する。"""

from datetime import UTC, datetime

import httpx

from app.api.auth import get_current_user
from app.api.routers.spots import get_catalog_repository
from app.domains.catalog import SpotData, SpotsSnapshot
from app.domains.users import UserData
from app.main import create_app


class MemoryCatalogRepository:
    async def list_spots(self) -> SpotsSnapshot:
        return SpotsSnapshot(
            spots=[
                SpotData(
                    spot_id=f"spot_{index:03d}",
                    kind="poi" if index <= 30 else "facility",
                    category="tourist_spot",
                    name_ja=f"スポット {index}",
                    tags_ja=["自然"],
                    lat=39.0 + index / 1000,
                    lon=140.0 + index / 1000,
                    social_proof=None,
                )
                for index in range(1, 44)
            ],
            latest_updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )


async def test_spots_returns_43_and_honors_etag() -> None:
    now = datetime.now(UTC)
    current_user = UserData(1, "p01", "token", None, now, now)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_catalog_repository] = MemoryCatalogRepository

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/spots")
        cached = await client.get(
            "/api/v1/spots", headers={"If-None-Match": first.headers["etag"]}
        )

    assert first.status_code == 200
    assert len(first.json()) == 43
    assert first.json()[0]["spot_id"] == "spot_001"
    assert first.headers["etag"].startswith('"')
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == first.headers["etag"]
