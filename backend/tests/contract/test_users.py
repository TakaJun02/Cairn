"""login → Bearer → thread の外部契約とユーザー境界を検証する。"""

from datetime import UTC, datetime

import httpx

from app.api.auth import get_user_repository
from app.api.routers.spots import get_catalog_repository
from app.domains.catalog import SpotsSnapshot
from app.domains.users import MessageData, ProfileData, ThreadData, UserData
from app.domains.users.repo import _public_pending
from app.main import create_app


class MemoryUserRepository:
    def __init__(self) -> None:
        self.next_id = 1
        self.commits = 0
        self.users_by_name: dict[str, UserData] = {}
        self.users_by_token: dict[str, UserData] = {}
        self.profiles: dict[int, ProfileData] = {}
        self.threads: dict[int, ThreadData] = {}

    async def commit(self) -> None:
        self.commits += 1

    async def find_by_name(self, user_name: str) -> UserData | None:
        return self.users_by_name.get(user_name)

    async def find_by_token(self, token: str) -> UserData | None:
        return self.users_by_token.get(token)

    async def create_or_get(self, user_name: str, api_token: str) -> tuple[UserData, bool]:
        existing = self.users_by_name.get(user_name)
        if existing is not None:
            await self.ensure_context(existing.id)
            return existing, False
        now = datetime.now(UTC)
        user = UserData(
            id=self.next_id,
            user_name=user_name,
            api_token=api_token,
            lora_device_id=None,
            created_at=now,
            updated_at=now,
        )
        self.next_id += 1
        self.users_by_name[user_name] = user
        self.users_by_token[api_token] = user
        await self.ensure_context(user.id)
        return user, True

    async def ensure_context(self, user_id: int) -> None:
        if user_id not in self.profiles:
            profile = ProfileData(
                user_id=user_id,
                interests={},
                party=None,
                mobility=None,
                pace=None,
                avoid=[],
                liked_spots=[],
                rejected_spots=[],
                notes=None,
                updated_at=datetime.now(UTC),
            )
            self.profiles[user_id] = profile
            self.threads[user_id] = ThreadData(
                messages=[],
                itinerary=None,
                profile=profile,
                pending=None,
            )

    async def get_profile(self, user_id: int) -> ProfileData:
        await self.ensure_context(user_id)
        return self.profiles[user_id]

    async def get_thread(self, user_id: int) -> ThreadData:
        await self.ensure_context(user_id)
        return self.threads[user_id]


class CommitGatedMemoryUserRepository(MemoryUserRepository):
    """commit 前のトークンを別リクエストから不可視にするテストダブル。"""

    def __init__(self) -> None:
        super().__init__()
        self.visible_tokens: set[str] = set()

    async def find_by_token(self, token: str) -> UserData | None:
        if token not in self.visible_tokens:
            return None
        return await super().find_by_token(token)

    async def commit(self) -> None:
        await super().commit()
        self.visible_tokens.update(self.users_by_token)


class EmptyCatalogRepository:
    async def list_spots(self) -> SpotsSnapshot:
        return SpotsSnapshot(spots=[], latest_updated_at=None)


def test_pending_ask_is_restored_with_reason_and_unchanged_public_shapes() -> None:
    preference = _public_pending(
        {
            "kind": "preference",
            "slot": "origin",
            "reason": "仮定した旅程条件の確認",
            "options": [
                {"label": "この条件で進める", "value": "accept_assumptions"},
                {"label": "条件を変更する", "value": "change_conditions"},
            ],
        }
    )
    clarify = _public_pending(
        {
            "kind": "clarify",
            "surface": "2番目",
            "reason": "候補が複数あります",
            "options": [
                {"label": "鶴間池", "value": "spot_001"},
                {"label": "元滝伏流水", "value": "spot_002"},
            ],
        }
    )

    assert preference == {
        "kind": "ask_user",
        "slot": "origin",
        "reason": "仮定した旅程条件の確認",
        "options": ["この条件で進める", "条件を変更する"],
    }
    assert clarify == {
        "kind": "clarify",
        "surface": "2番目",
        "reason": "候補が複数あります",
        "options": [
            {"label": "鶴間池", "value": "spot_001"},
            {"label": "元滝伏流水", "value": "spot_002"},
        ],
    }


async def test_thread_response_includes_pending_reason() -> None:
    repository = MemoryUserRepository()
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: repository

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post("/api/v1/login", json={"user_name": "pending-user"})
        identity = login.json()
        profile = repository.profiles[identity["user_id"]]
        repository.threads[identity["user_id"]] = ThreadData(
            messages=[],
            itinerary=None,
            profile=profile,
            pending={
                "kind": "ask_user",
                "slot": "origin",
                "reason": "仮定した旅程条件の確認",
                "options": ["この条件で進める", "条件を変更する"],
            },
        )
        thread = await client.get(
            "/api/v1/thread",
            headers={"Authorization": f"Bearer {identity['token']}"},
        )

    assert thread.status_code == 200
    assert thread.json()["pending"] == {
        "kind": "ask_user",
        "slot": "origin",
        "reason": "仮定した旅程条件の確認",
        "options": ["この条件で進める", "条件を変更する"],
    }


async def test_login_commits_token_before_immediate_authenticated_request() -> None:
    repository = CommitGatedMemoryUserRepository()
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: repository
    app.dependency_overrides[get_catalog_repository] = EmptyCatalogRepository

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post("/api/v1/login", json={"user_name": "race"})
        token = login.json()["token"]
        spots = await client.get(
            "/api/v1/spots",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert login.status_code == 200
    assert spots.status_code == 200
    assert repository.commits == 1


async def test_login_token_opens_thread_and_missing_token_is_401() -> None:
    repository = MemoryUserRepository()
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: repository

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post("/api/v1/login", json={"user_name": "p01"})
        token = login.json()["token"]
        anonymous = await client.get("/api/v1/thread")
        thread = await client.get(
            "/api/v1/thread", headers={"Authorization": f"Bearer {token}"}
        )
        repeated_login = await client.post("/api/v1/login", json={"user_name": "p01"})

    assert login.status_code == 200
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == "Bearer"
    assert thread.status_code == 200
    assert thread.json()["messages"] == []
    assert thread.json()["itinerary"] is None
    assert thread.json()["pending"] is None
    assert repeated_login.json() == login.json()
    assert repository.commits == 2


async def test_explicit_registration_returns_token_and_rejects_duplicate() -> None:
    repository = MemoryUserRepository()
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: repository

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post("/api/v1/users", json={"user_name": "new-user"})
        duplicate = await client.post("/api/v1/users", json={"user_name": "new-user"})

    assert created.status_code == 201
    assert created.json()["user_name"] == "new-user"
    assert len(created.json()["token"]) >= 43
    assert duplicate.status_code == 409
    assert repository.commits == 1


async def test_token_always_scopes_thread_and_me_to_its_owner() -> None:
    repository = MemoryUserRepository()
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: repository

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        alice = (await client.post("/api/v1/login", json={"user_name": "alice"})).json()
        bob = (await client.post("/api/v1/login", json={"user_name": "bob"})).json()
        message_time = datetime.now(UTC)
        repository.threads[alice["user_id"]] = ThreadData(
            messages=[MessageData(1, 1, "user", "alice-only", {}, message_time)],
            itinerary=None,
            profile=repository.profiles[alice["user_id"]],
            pending=None,
        )
        repository.threads[bob["user_id"]] = ThreadData(
            messages=[MessageData(2, 1, "user", "bob-only", {}, message_time)],
            itinerary=None,
            profile=repository.profiles[bob["user_id"]],
            pending=None,
        )
        alice_thread = await client.get(
            "/api/v1/thread",
            headers={"Authorization": f"Bearer {alice['token']}"},
        )
        bob_thread = await client.get(
            "/api/v1/thread",
            headers={"Authorization": f"Bearer {bob['token']}"},
        )
        bob_me = await client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {bob['token']}"},
        )
        invalid = await client.get(
            "/api/v1/thread",
            headers={"Authorization": "Bearer not-bobs-token"},
        )

    assert alice_thread.status_code == 200
    assert alice_thread.json()["profile"] == {
        "interests": {},
        "party": None,
        "mobility": None,
        "pace": None,
        "avoid": [],
        "liked_spots": [],
        "rejected_spots": [],
        "notes": None,
        "updated_at": repository.profiles[alice["user_id"]].updated_at.isoformat().replace(
            "+00:00", "Z"
        ),
    }
    assert bob_me.status_code == 200
    assert bob_me.json()["user_id"] == bob["user_id"]
    assert bob_me.json()["user_name"] == "bob"
    assert bob_me.json()["user_id"] != alice["user_id"]
    assert alice_thread.json()["messages"][0]["content"] == "alice-only"
    assert bob_thread.json()["messages"][0]["content"] == "bob-only"
    assert "alice-only" not in bob_thread.text
    assert invalid.status_code == 401
