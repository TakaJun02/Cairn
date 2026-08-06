"""旅程 GET / undo / redo の API 契約。"""

from dataclasses import replace
from datetime import UTC, datetime

import httpx

from app.api.auth import get_current_user
from app.api.routers.itinerary import get_itinerary_repository
from app.api.schemas.chat import ChatEvent
from app.domains.conversation.itinerary_digest import UNNAMED_SPOT_JA
from app.domains.itinerary.repo_types import (
    ItineraryNotFoundError,
    ItineraryVersion,
    ItineraryVersionConflictError,
    PlanningData,
)
from app.domains.itinerary.solver import TravelTimeMatrix
from app.domains.itinerary.types import Concession, Itinerary, PredEnum
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


def _version_with_legacy_concession(
    version: int, parent: int | None, *, message_ja: str
) -> ItineraryVersion:
    """[25 §1-7] F8: 旧形式(message_ja に生の spot_id が入っている)の

    concession を持つ版。送出層(`_itinerary_state`)のマスクが undo/GET の
    トップレベル `concessions` と `itinerary.concessions` の両方に効くこと
    を確認するために使う。
    """

    return ItineraryVersion(
        user_id=1,
        version=version,
        parent_version=parent,
        is_current=False,
        itinerary=Itinerary(
            days=[],
            concessions=[
                Concession(
                    constraint_id="c_001",
                    pred=PredEnum.REQUIRE,
                    args={"target": "spot_014"},
                    violation=1.0,
                    message_ja=message_ja,
                )
            ],
            version=version,
        ),
        constraints=[],
        origin="plan" if version == 1 else "edit",
        created_by_message_id=None,
    )


class MemoryItineraryRepository:
    def __init__(self) -> None:
        self.versions = {1: _version(1, None), 2: _version(2, 1)}
        self.current_version = 2
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

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

    async def load_planning_data(self) -> PlanningData:
        # `_itinerary_state`([25 §1-7])が spot_id → name_ja の解決に使う。
        # このテストの旅程は空(`days=[]`)なので中身は空でよい。
        return PlanningData(spots={}, travel_times=TravelTimeMatrix({}))

    async def load_spot_names(self) -> dict[str, str]:
        # 2026-08-04 追加([25 §1-7] F6・F8): `_spot_names` が undo/redo・
        # GET の concessions マスクに使う軽量メソッド。このテストでは
        # spot_014 の名前を意図的に登録しない(未登録地点として中立表記
        # `UNNAMED_SPOT_JA` に落ちることを確認するため)。
        return {}


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
    assert repository.commits == 2


async def test_get_and_undo_mask_legacy_spot_id_concessions() -> None:
    """[25 §1-7] レビュー是正 F8: 旧形式 concession(message_ja に生の

    spot_id が残っている版)を GET/undo 応答で送出するとき、トップレベル
    `concessions` と `itinerary.concessions` の両方で `message_ja` が
    マスクされる(spot_id を含まない)ことを確認する。
    `MemoryItineraryRepository` の旅程は元々 concessions が空だったため、
    このマスク(`_itinerary_state` → `mask_concession_list`)は一度も
    実行されていなかった。
    """

    repository = MemoryItineraryRepository()
    legacy_message = "必須希望の「spot_014」を旅程に入れられませんでした"
    repository.versions[1] = _version_with_legacy_concession(1, None, message_ja=legacy_message)
    repository.versions[2] = _version_with_legacy_concession(2, 1, message_ja=legacy_message)
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

    for response in (current, undone):
        assert response.status_code == 200
        body = response.json()
        assert body["concessions"], "concessions が空では検証にならない"
        assert body["itinerary"]["concessions"], "itinerary.concessions が空では検証にならない"
        top_messages = [item["message_ja"] for item in body["concessions"]]
        nested_messages = [item["message_ja"] for item in body["itinerary"]["concessions"]]
        for message in (*top_messages, *nested_messages):
            assert "spot_" not in message
        assert top_messages == [f"必須希望の「{UNNAMED_SPOT_JA}」を旅程に入れられませんでした"]
        assert nested_messages == top_messages
