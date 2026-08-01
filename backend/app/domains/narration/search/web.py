"""Tavily Web 検索とプロセス内サーキットブレーカー。"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings, get_settings

TAVILY_ENDPOINT = "https://api.tavily.com/search"
CB_FAILURE_THRESHOLD = 3
CB_RECOVERY_SECONDS = 60.0


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    content: str
    raw_content: str | None = None


@dataclass(frozen=True)
class WebSearchResponse:
    available: bool
    hits: tuple[WebSearchHit, ...] = ()
    message: str = ""


@dataclass
class CircuitBreakerState:
    consecutive_failures: int = 0
    opened_at: float | None = None


_SHARED_CIRCUIT_STATE = CircuitBreakerState()


class WebSearchProvider(Protocol):
    async def search(
        self,
        queries: Sequence[str],
        *,
        include_raw_content: bool,
    ) -> WebSearchResponse: ...


class TavilyWebSearchClient:
    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
        *,
        failure_threshold: int = CB_FAILURE_THRESHOLD,
        recovery_seconds: float = CB_RECOVERY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        circuit_state: CircuitBreakerState | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client
        self._owned_client: httpx.AsyncClient | None = None
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.clock = clock
        self._circuit = circuit_state or _SHARED_CIRCUIT_STATE

    async def search(
        self,
        queries: Sequence[str],
        *,
        include_raw_content: bool,
    ) -> WebSearchResponse:
        if not self.settings.tavily_api_key:
            return WebSearchResponse(
                available=False,
                message="TAVILY_API_KEY が未設定のため Web 検索は利用不可です。",
            )
        if self._circuit_is_open():
            return WebSearchResponse(
                available=False,
                message="サーキットブレーカー開放中のため Web 検索は現在利用不可です。",
            )

        hits: dict[str, WebSearchHit] = {}
        last_error: Exception | None = None
        for query in queries:
            try:
                response = await self._client().post(
                    TAVILY_ENDPOINT,
                    json={
                        "api_key": self.settings.tavily_api_key,
                        "query": query,
                        "search_depth": "advanced",
                        "max_results": 5,
                        "include_answer": False,
                        "include_raw_content": include_raw_content,
                    },
                )
                response.raise_for_status()
                for hit in _parse_results(response.json()):
                    hits.setdefault(hit.url, hit)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                break

        if last_error is not None:
            self._record_failure()
            if not hits:
                return WebSearchResponse(
                    available=False,
                    message=(
                        "Tavily への接続に失敗したため Web 検索は現在利用不可です "
                        f"({type(last_error).__name__})。"
                    ),
                )
            return WebSearchResponse(
                available=True,
                hits=tuple(hits.values()),
                message="一部の Web 検索だけ取得できました。",
            )

        self._circuit.consecutive_failures = 0
        self._circuit.opened_at = None
        return WebSearchResponse(available=True, hits=tuple(hits.values()))

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        return self._owned_client

    def _circuit_is_open(self) -> bool:
        if self._circuit.opened_at is None:
            return False
        if self.clock() - self._circuit.opened_at < self.recovery_seconds:
            return True
        self._circuit.opened_at = None
        self._circuit.consecutive_failures = 0
        return False

    def _record_failure(self) -> None:
        self._circuit.consecutive_failures += 1
        if self._circuit.consecutive_failures >= self.failure_threshold:
            self._circuit.opened_at = self.clock()


def _parse_results(payload: Any) -> list[WebSearchHit]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Tavily results が配列ではありません")
    parsed: list[WebSearchHit] = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            raise TypeError("Tavily result がオブジェクトではありません")
        title = item.get("title")
        url = item.get("url")
        content = item.get("content", "")
        raw_content = item.get("raw_content")
        if not isinstance(title, str) or not isinstance(url, str):
            raise TypeError("Tavily result の title/url が文字列ではありません")
        if not isinstance(content, str):
            content = ""
        if not isinstance(raw_content, str):
            raw_content = None
        parsed.append(
            WebSearchHit(
                title=title,
                url=url,
                content=content,
                raw_content=raw_content,
            )
        )
    return parsed
