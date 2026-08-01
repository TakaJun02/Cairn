"""POST /chat の認証、順序、失敗、同時実行、切断後復元を検証する。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.api.auth import get_user_repository
from app.api.routers.chat import (
    ActiveTurnRegistry,
    get_active_turn_registry,
    get_chat_turn_runner,
)
from app.core.config import Settings, get_settings
from app.domains.conversation.events import ConversationEvent
from app.domains.users import MessageData, ProfileData, ThreadData, UserData
from app.main import create_app


class MemoryChatUserRepository:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.user = UserData(1, "chat-user", "chat-token", None, now, now)
        self.profile = ProfileData(1, {}, None, None, None, [], [], [], None, now)
        self.thread = ThreadData([], None, self.profile, None)

    async def find_by_token(self, token: str) -> UserData | None:
        return self.user if token == self.user.api_token else None

    async def get_thread(self, user_id: int) -> ThreadData:
        assert user_id == self.user.id
        return self.thread


class ScriptedRunner:
    async def __call__(self, *, event_sink: Any, **kwargs: Any) -> None:
        del kwargs
        events = [
            ConversationEvent(
                event="state",
                data={
                    "kind": "plan",
                    "steps": [{"id": 1, "tool": "recommend"}],
                },
            ),
            ConversationEvent(
                event="state",
                data={
                    "kind": "candidates",
                    "stage": "provisional",
                    "spot_ids": ["spot_012"],
                    "candidates": [
                        {
                            "spot_id": "spot_012",
                            "name_ja": "鶴間池",
                            "reason_materials": {},
                        }
                    ],
                },
            ),
            ConversationEvent(event="token", data={"text": "おすすめです"}),
            ConversationEvent(
                event="done",
                data={"turn_id": "turn-1", "message_id": 88, "degraded": False},
            ),
        ]
        for event in events:
            await event_sink.emit(event)


class FailingRunner:
    async def __call__(self, *, event_sink: Any, **kwargs: Any) -> None:
        del kwargs
        await event_sink.emit(
            ConversationEvent(
                event="state",
                data={
                    "kind": "plan",
                    "steps": [{"id": 1, "tool": "recommend"}],
                },
            )
        )
        raise RuntimeError("boom")


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, *, event_sink: Any, **kwargs: Any) -> None:
        del kwargs
        self.started.set()
        await self.release.wait()
        await event_sink.emit(
            ConversationEvent(
                event="done",
                data={"turn_id": "blocking", "message_id": 1, "degraded": False},
            )
        )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        POSTGRES_PASSWORD="test",
        CHAT_SSE_HEARTBEAT_SEC=0.05,
    )


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block or block.startswith(":"):
            continue
        lines = block.splitlines()
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        result.append((event, data))
    return result


def _app(repository: MemoryChatUserRepository, runner: Any):
    app = create_app()
    registry = ActiveTurnRegistry()

    async def user_repository_override() -> MemoryChatUserRepository:
        return repository

    async def runner_override() -> Any:
        return runner

    async def settings_override() -> Settings:
        return _settings()

    async def registry_override() -> ActiveTurnRegistry:
        return registry

    app.dependency_overrides[get_user_repository] = user_repository_override
    app.dependency_overrides[get_chat_turn_runner] = runner_override
    app.dependency_overrides[get_settings] = settings_override
    app.dependency_overrides[get_active_turn_registry] = registry_override
    return app


async def test_chat_headers_event_order_done_once_and_authentication() -> None:
    repository = MemoryChatUserRepository()
    app = _app(repository, ScriptedRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        anonymous = await client.post("/api/v1/chat", json={"message": "滝を見たい"})
        response = await client.post(
            "/api/v1/chat",
            json={"message": "滝を見たい"},
            headers={"Authorization": "Bearer chat-token", "Accept": "text/event-stream"},
        )
        invalid = await client.post(
            "/api/v1/chat",
            json={"message": "", "thread_id": "client-owned"},
            headers={"Authorization": "Bearer chat-token"},
        )

    events = _events(response.text)
    assert anonymous.status_code == 401
    assert invalid.status_code == 422
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert [name for name, _ in events] == ["state", "state", "token", "done"]
    assert events[1][1]["phase"] == "provisional"
    assert "items" in events[1][1]
    assert sum(name == "done" for name, _ in events) == 1
    assert events[-1][0] == "done"


async def test_stream_failure_stays_http_200_and_ends_error_then_done() -> None:
    repository = MemoryChatUserRepository()
    app = _app(repository, FailingRunner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "失敗させる"},
            headers={"Authorization": "Bearer chat-token"},
        )

    events = _events(response.text)
    assert response.status_code == 200
    assert [name for name, _ in events] == ["state", "error", "done"]
    assert events[-2][1]["code"] == "stream_failed"
    assert events[-2][1]["degraded"] is False


async def test_same_user_second_turn_is_409_while_first_is_running() -> None:
    repository = MemoryChatUserRepository()
    runner = BlockingRunner()
    app = _app(repository, runner)
    headers = {"Authorization": "Bearer chat-token"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first_task = asyncio.create_task(
            client.post("/api/v1/chat", json={"message": "1つ目"}, headers=headers)
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)
        second = await client.post(
            "/api/v1/chat",
            json={"message": "2つ目"},
            headers=headers,
        )
        runner.release.set()
        first = await asyncio.wait_for(first_task, timeout=1)

    assert second.status_code == 409
    assert first.status_code == 200


async def test_disconnect_keeps_worker_and_thread_can_restore_completed_turn() -> None:
    repository = MemoryChatUserRepository()
    persisted = asyncio.Event()

    async def runner(
        *,
        event_sink: Any,
        disconnected: asyncio.Event,
        **kwargs: Any,
    ) -> None:
        del kwargs
        await disconnected.wait()
        now = datetime.now(UTC)
        repository.thread = ThreadData(
            [MessageData(1, 1, "assistant", "切断後も保存済み", {}, now)],
            None,
            repository.profile,
            None,
        )
        await event_sink.emit(
            ConversationEvent(
                event="done",
                data={"turn_id": "detached", "message_id": 1, "degraded": False},
            )
        )
        persisted.set()

    app = _app(repository, runner)
    body = json.dumps({"message": "途中で切断"}).encode()
    receives: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    receives.put_nowait({"type": "http.request", "body": body, "more_body": False})
    receives.put_nowait({"type": "http.disconnect"})

    async def receive() -> dict[str, Any]:
        return await receives.get()

    async def send(message: dict[str, Any]) -> None:
        del message

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/chat",
            "raw_path": b"/api/v1/chat",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"authorization", b"Bearer chat-token"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    await asyncio.wait_for(persisted.wait(), timeout=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        restored = await client.get(
            "/api/v1/thread",
            headers={"Authorization": "Bearer chat-token"},
        )

    assert restored.status_code == 200
    assert restored.json()["messages"][0]["content"] == "切断後も保存済み"
