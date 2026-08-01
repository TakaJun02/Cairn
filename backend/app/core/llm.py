"""OpenAI 互換の生成 API とモデル固有後処理。"""

import asyncio
import re
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import Settings, get_settings

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", flags=re.IGNORECASE | re.DOTALL)
_THINK_MARKER = re.compile(r"</?think>", flags=re.IGNORECASE)


class GenerationError(RuntimeError):
    """生成 API が応答を返せなかったことを表す。"""


def normalize_generated_text(value: str) -> str:
    """`<think>` など、画面へ出さないモデル固有表現を除去する。"""

    without_blocks = _THINK_BLOCK.sub("", value)
    return _THINK_MARKER.sub("", without_blocks).strip()


class GenerationClient:
    """httpx を用いる小さな OpenAI 互換クライアント。"""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str:
        """チャット補完を呼ぶ。

        再試行後も失敗した場合だけ例外にする。
        """

        if not self.settings.inference_model:
            raise GenerationError("INFERENCE_MODEL が設定されていません")

        payload: dict[str, Any] = {
            "model": self.settings.inference_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if extra_body:
            payload.update(extra_body)

        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.inference_timeout_sec)
        )
        last_error: Exception | None = None
        try:
            async with asyncio.timeout(self.settings.inference_outer_timeout_sec):
                for attempt in range(self.settings.inference_max_attempts):
                    try:
                        response = await client.post(
                            f"{self.settings.inference_server.rstrip('/')}/chat/completions",
                            json=payload,
                        )
                        response.raise_for_status()
                        content = response.json()["choices"][0]["message"]["content"]
                        if not isinstance(content, str):
                            raise TypeError("choices[0].message.content が文字列ではありません")
                        return normalize_generated_text(content)
                    except httpx.HTTPStatusError as exc:
                        last_error = exc
                        status = exc.response.status_code
                        if status not in {408, 409, 425, 429} and status < 500:
                            break
                    except (
                        httpx.TransportError,
                        KeyError,
                        IndexError,
                        TypeError,
                        ValueError,
                    ) as exc:
                        last_error = exc
                        if not isinstance(exc, httpx.TransportError):
                            break

                    if attempt + 1 < self.settings.inference_max_attempts:
                        await asyncio.sleep(2**attempt)
        except TimeoutError as exc:
            last_error = exc
        finally:
            if owns_client:
                await client.aclose()

        reason = type(last_error).__name__ if last_error is not None else "unknown"
        raise GenerationError(f"生成 API の呼び出しに失敗しました ({reason})") from last_error
