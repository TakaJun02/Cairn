"""モデル固有の出力後処理と SSE 境界を検証する。"""

from collections.abc import AsyncIterator

import httpx

from app.core.config import Settings
from app.core.llm import GenerationClient, normalize_generated_text


def test_normalize_generated_text_removes_think_blocks() -> None:
    value = "<think>内部推論\nをここへ書く</think>\n鳥海山をご案内します。"

    assert normalize_generated_text(value) == "鳥海山をご案内します。"


class _InterruptedThinkStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"<think>secret"}}]}\n\n'
        raise httpx.ReadError("interrupted before visible text")


async def test_stream_retry_does_not_carry_hidden_think_state() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=_InterruptedThinkStream())
        body = (
            'data: {"choices":[{"delta":{"content":"案内します"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body.encode())

    settings = Settings(
        inference_model="test-model",
        inference_timeout_sec=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GenerationClient(settings, http_client=http_client)
        chunks = [value async for value in client.stream([{"role": "user", "content": "x"}])]

    assert calls == 2
    assert "".join(chunks) == "案内します"
