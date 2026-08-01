"""Qwen3-Embedding 用の OpenAI 互換クライアント。"""

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import Settings, get_settings

EMBEDDING_DIMENSION = 4096


class EmbeddingError(RuntimeError):
    """埋め込み API が利用できない、または不正な応答を返した。"""


class OpenAIEmbeddingClient:
    """文書用テキストを OpenAI 互換 `/embeddings` へ送る。

    Qwen3 の instruct プレフィックスは検索クエリだけの責務であるため、
    この文書索引用クライアントでは一切付与しない。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client
        self._owned_client: httpx.AsyncClient | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """1 バッチを埋め込み、入力順に 4096 次元ベクトルを返す。"""

        if not texts:
            return []
        if not self.settings.embedding_server:
            raise EmbeddingError("EMBEDDING_SERVER が設定されていません")
        if self.settings.embedding_dim != EMBEDDING_DIMENSION:
            raise EmbeddingError(
                "EMBEDDING_DIM は DB の vector(4096) と一致させてください"
            )

        payload = {
            "model": self.settings.embedding_model,
            "input": list(texts),
            "encoding_format": "float",
        }
        client = self._client()
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await client.post(
                    f"{self.settings.embedding_server.rstrip('/')}/embeddings",
                    json=payload,
                )
                response.raise_for_status()
                return _parse_embeddings(response.json(), len(texts))
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                if status_code < 500 and status_code != 429:
                    break
            except (httpx.TransportError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                if not isinstance(exc, httpx.TransportError):
                    break

            if attempt == 0:
                await asyncio.sleep(1)

        reason = type(last_error).__name__ if last_error is not None else "unknown"
        raise EmbeddingError(f"埋め込み API の呼び出しに失敗しました ({reason})") from last_error

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.embedding_timeout_sec)
            )
        return self._owned_client


def _parse_embeddings(payload: Any, expected_count: int) -> list[list[float]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("data が配列ではありません")

    ordered: list[list[float] | None] = [None] * expected_count
    for fallback_index, item in enumerate(payload["data"]):
        if not isinstance(item, dict):
            raise TypeError("data の要素がオブジェクトではありません")
        index = item.get("index", fallback_index)
        vector = item.get("embedding")
        if not isinstance(index, int) or not 0 <= index < expected_count:
            raise ValueError("embedding index が入力範囲外です")
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSION:
            raise ValueError("embedding が 4096 次元ではありません")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in vector):
            raise TypeError("embedding に数値以外が含まれています")
        ordered[index] = [float(value) for value in vector]

    if any(vector is None for vector in ordered):
        raise ValueError("embedding の件数が入力件数と一致しません")
    return [vector for vector in ordered if vector is not None]
