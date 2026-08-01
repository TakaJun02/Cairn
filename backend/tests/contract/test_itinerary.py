"""旅程 GET / undo / redo の API 契約。"""

from dataclasses import replace
from datetime import UTC, datetime

import httpx

from app.api.auth import get_current_user
from app.api.routers.itinerary import get_itinerary_repository
from app.api.schemas.chat import ChatEvent
from app.domains.itinerary.repo_types import (
    ItineraryNotFoundError,
    ItineraryVersion,
    ItineraryVersionConflictError,
)
from app.domains.itinerary.types import Itinerary
from app.domains.users import UserData
from app.main import create_app


def _version(version: int, parent: int | None) -> ItineraryVersion:
    return ItineraryVersion(
        user_id=1,
        version=version,
        parent_version=parent,
        is_current=False,
        itinerary=Itinerary(days=[], concessions=[], version=version),
        constraints=[],
        origin="plan" if version == 1 else "edit",
        created_by_message_id=None,
    )


class MemoryItineraryRepository:
    def __init__(self) -> None:
        self.versions = {1: _version(1, None), 2: _version(2, 1)}
        self.current_version = 2

    async def get_current(self, user_id: int):
        assert user_id == 1
        value = self.versions[self.current_version]
        return replace(value, is_current=True)

    async def get_version(self, user_id: int, version: int):
        assert user_id == 1
        return self.versions.get(version)

    async def revert(self, user_id: int, *, expected_current_version: int):
        if expected_current_version != self.current_version:
            raise ItineraryVersionConflictError("現在版が変わりました")
        current = self.versions[self.current_version]
        if current.parent_version is None:
            raise ItineraryNotFoundError("これ以上前の旅程版はありません")
        self.current_version = current.parent_version
        return await self.get_current(user_id)

    async def redo(self, user_id: int, *, expected_current_version: int):
        if expected_current_version != self.current_version:
            raise ItineraryVersionConflictError("現在版が変わりました")
        children = [
            value.version
            for value in self.versions.values()
            if value.parent_version == self.current_version
        ]
        if len(children) != 1:
            raise ItineraryNotFoundError("やり直せる旅程版がありません")
        self.current_version = children[0]
        return await self.get_current(user_id)


async def test_get_undo_redo_share_itinerary_state_and_use_optimistic_lock() -> None:
    repository = MemoryItineraryRepository()
    now = datetime.now(UTC)
    user = UserData(1, "itinerary-user", "token", None, now, now)
    app = create_app()

    async def current_user_override() -> UserData:
        return user

    async def repository_override() -> MemoryItineraryRepository:
        return repository

    app.dependency_overrides[get_current_user] = current_user_override
    app.dependency_overrides[get_itinerary_repository] = repository_override

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        current = await client.get("/api/v1/itinerary")
        undone = await client.post(
            "/api/v1/itinerary/undo",
            json={"expected_current_version": 2},
        )
        duplicate_undo = await client.post(
            "/api/v1/itinerary/undo",
            json={"expected_current_version": 2},
        )
        no_earlier_version = await client.post(
            "/api/v1/itinerary/undo",
            json={"expected_current_version": 1},
        )
        redone = await client.post(
            "/api/v1/itinerary/redo",
            json={"expected_current_version": 1},
        )

    for response in (current, undone, redone):
        assert response.status_code == 200
        ChatEvent.model_validate({"event": "state", "data": response.json()})
        assert response.json()["kind"] == "itinerary"
        assert response.json()["phase"] == "final"
    assert current.json()["version"] == 2
    assert undone.json()["version"] == 1
    assert redone.json()["version"] == 2
    assert duplicate_undo.status_code == 409
    assert duplicate_undo.json()["detail"]["current_version"] == 1
    assert no_earlier_version.status_code == 409
    assert no_earlier_version.json()["detail"]["current_version"] == 1
