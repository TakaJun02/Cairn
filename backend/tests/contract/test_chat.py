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
from app.domains.conversation.ask_registry import AskUserRegistry, wait_for_answer
from app.domains.conversation.events import ConversationEvent
from app.domains.users import MessageData, ProfileData, ThreadData, UserData
from app.main import create_app


class MemoryChatUserRepository:
    def __init__(self, *, pending: dict[str, Any] | None = None) -> None:
        now = datetime.now(UTC)
        self.user = UserData(1, "chat-user", "chat-token", None, now, now)
        self.profile = ProfileData(1, {}, None, None, None, [], [], [], None, now)
        self.thread = ThreadData([], None, self.profile, pending)

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
                    "kind": "step",
                    "tool": "recommend",
                    "status": "started",
                    "label_ja": "おすすめを探しています",
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
                    "kind": "step",
                    "tool": "recommend",
                    "status": "started",
                    "label_ja": "おすすめを探しています",
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


# ---------------------------------------------------------------------------
# POST /api/v1/chat/answer(§1.4: ask_user への回答。HITL)
# ---------------------------------------------------------------------------


async def test_chat_answer_resolves_the_waiting_turn_with_free_text() -> None:
    repository = MemoryChatUserRepository()
    app = _app(repository, ScriptedRunner())
    registry = AskUserRegistry()
    app.state.ask_registry = registry

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        waiter = asyncio.create_task(wait_for_answer(registry, key=1, timeout_sec=2))
        await asyncio.sleep(0)  # begin() が呼ばれるまで進める
        response = await client.post(
            "/api/v1/chat/answer",
            json={"answer": "30分程度なら"},
            headers={"Authorization": "Bearer chat-token"},
        )
        answer = await waiter

    assert response.status_code == 204
    assert response.content == b""
    assert answer is not None
    assert answer.answer == "30分程度なら"
    assert answer.answered_by == "free_text"
    assert answer.resolves is None


_CLARIFY_PENDING = {
    "kind": "clarify",
    "surface": "2番目",
    "reason": "候補が複数あります",
    "options": [
        {"label": "鶴間池", "value": "spot_012"},
        {"label": "元滝伏流水", "value": "spot_007"},
    ],
}

_PREFERENCE_PENDING = {
    "kind": "ask_user",
    "slot": "mobility",
    "reason": "どのくらい歩けますか",
    # `state:ask_user` の options はラベル文字列のみ(§1.2)。フロントは
    # value にラベルそのものを送る(`lib/askAnswer.js`)。
    "options": ["あまり歩きたくない", "30分程度なら"],
}


async def test_chat_answer_with_resolves_is_recorded_as_chip() -> None:
    repository = MemoryChatUserRepository(pending=_CLARIFY_PENDING)
    app = _app(repository, ScriptedRunner())
    registry = AskUserRegistry()
    app.state.ask_registry = registry

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        waiter = asyncio.create_task(wait_for_answer(registry, key=1, timeout_sec=2))
        await asyncio.sleep(0)
        response = await client.post(
            "/api/v1/chat/answer",
            json={"answer": "鶴間池", "resolves": {"surface": "2番目", "value": "spot_012"}},
            headers={"Authorization": "Bearer chat-token"},
        )
        answer = await waiter

    assert response.status_code == 204
    assert answer is not None
    assert answer.answered_by == "chip"
    assert answer.resolves == {"surface": "2番目", "value": "spot_012"}


async def test_chat_answer_with_slot_resolves_is_also_a_chip() -> None:
    repository = MemoryChatUserRepository(pending=_PREFERENCE_PENDING)
    app = _app(repository, ScriptedRunner())
    registry = AskUserRegistry()
    app.state.ask_registry = registry

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        waiter = asyncio.create_task(wait_for_answer(registry, key=1, timeout_sec=2))
        await asyncio.sleep(0)
        response = await client.post(
            "/api/v1/chat/answer",
            json={
                "answer": "30分程度なら",
                "resolves": {"slot": "mobility", "value": "30分程度なら"},
            },
            headers={"Authorization": "Bearer chat-token"},
        )
        answer = await waiter

    assert response.status_code == 204
    assert answer is not None
    assert answer.answered_by == "chip"
    assert answer.resolves == {"slot": "mobility", "value": "30分程度なら"}


async def test_chat_answer_resolves_for_a_stale_clarify_question_is_409() -> None:
    """裁定5(2026-08-04レビュー是正): Q1 への遅延回答が Q2 登録後に届くと

    409 で拒否される(古い質問の `surface` は現在の `pending` と一致しない)。
    """

    repository = MemoryChatUserRepository(pending=_CLARIFY_PENDING)
    app = _app(repository, ScriptedRunner())
    registry = AskUserRegistry()
    app.state.ask_registry = registry

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        waiter = asyncio.create_task(wait_for_answer(registry, key=1, timeout_sec=2))
        await asyncio.sleep(0)
        response = await client.post(
            "/api/v1/chat/answer",
            json={
                "answer": "元滝伏流水",
                # 現在の pending は "2番目" を聞いている。これは別の(古い)質問。
                "resolves": {"surface": "何日目に入れますか", "value": "spot_007"},
            },
            headers={"Authorization": "Bearer chat-token"},
        )
        # Future は解決されずに残るので、タイムアウトを待たずに片付ける。
        registry.end(1)

    assert response.status_code == 409
    assert waiter.cancelled() is False
    waiter.cancel()


async def test_chat_answer_resolves_with_unknown_value_is_409() -> None:
    """選択肢の value が現在の pending に存在しなければ 409(A4 相当の検査)。"""

    repository = MemoryChatUserRepository(pending=_PREFERENCE_PENDING)
    app = _app(repository, ScriptedRunner())
    registry = AskUserRegistry()
    app.state.ask_registry = registry

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        waiter = asyncio.create_task(wait_for_answer(registry, key=1, timeout_sec=2))
        await asyncio.sleep(0)
        response = await client.post(
            "/api/v1/chat/answer",
            json={
                "answer": "山登りもしたい",
                "resolves": {"slot": "mobility", "value": "山登りもしたい"},
            },
            headers={"Authorization": "Bearer chat-token"},
        )
        registry.end(1)

    assert response.status_code == 409
    waiter.cancel()


async def test_chat_answer_resolves_without_any_pending_is_409() -> None:
    """`pending` が無い(既に回答済み・掃除済み)のに `resolves` 付きで来たら 409。"""

    repository = MemoryChatUserRepository(pending=None)
    app = _app(repository, ScriptedRunner())
    registry = AskUserRegistry()
    app.state.ask_registry = registry

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        waiter = asyncio.create_task(wait_for_answer(registry, key=1, timeout_sec=2))
        await asyncio.sleep(0)
        response = await client.post(
            "/api/v1/chat/answer",
            json={"answer": "鶴間池", "resolves": {"surface": "2番目", "value": "spot_012"}},
            headers={"Authorization": "Bearer chat-token"},
        )
        registry.end(1)

    assert response.status_code == 409
    waiter.cancel()


async def test_chat_answer_without_a_waiting_turn_is_409() -> None:
    repository = MemoryChatUserRepository()
    app = _app(repository, ScriptedRunner())
    app.state.ask_registry = AskUserRegistry()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat/answer",
            json={"answer": "回答したいけど質問はまだ来ていない"},
            headers={"Authorization": "Bearer chat-token"},
        )

    assert response.status_code == 409


async def test_chat_answer_requires_authentication() -> None:
    repository = MemoryChatUserRepository()
    app = _app(repository, ScriptedRunner())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat/answer",
            json={"answer": "はい"},
        )

    assert response.status_code == 401


async def test_chat_stays_409_while_the_turn_is_waiting_for_an_ask_user_answer() -> None:
    """§1.4: 質問待ち中も実行中ターンのまま。`POST /chat` は 409 のまま。"""

    repository = MemoryChatUserRepository()
    started = asyncio.Event()

    async def waiting_runner(
        *,
        event_sink: Any,
        ask_registry: AskUserRegistry,
        **kwargs: Any,
    ) -> None:
        del kwargs
        started.set()
        await wait_for_answer(ask_registry, key=1, timeout_sec=2)
        await event_sink.emit(
            ConversationEvent(
                event="done",
                data={"turn_id": "waiting", "message_id": 1, "degraded": False},
            )
        )

    app = _app(repository, waiting_runner)
    headers = {"Authorization": "Bearer chat-token"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first_task = asyncio.create_task(
            client.post("/api/v1/chat", json={"message": "1つ目"}, headers=headers)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        second = await client.post(
            "/api/v1/chat", json={"message": "2つ目"}, headers=headers
        )
        answered = await client.post(
            "/api/v1/chat/answer",
            json={"answer": "30分程度なら"},
            headers=headers,
        )
        first = await asyncio.wait_for(first_task, timeout=1)

    assert second.status_code == 409
    assert answered.status_code == 204
    assert first.status_code == 200
