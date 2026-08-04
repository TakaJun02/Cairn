"""ffmpeg を介さず MP3 を返す gTTS 実装。"""

from __future__ import annotations

import asyncio
import io
import math
from collections.abc import Awaitable, Callable
from typing import BinaryIO, Protocol

from gtts import gTTS
from mutagen.mp3 import MP3

from app.domains.voice.port import Audio

GTTS_RETRY_COUNT = 3
GTTS_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
MP3_MIME = "audio/mpeg"


class _GTTSWriter(Protocol):
    def write_to_fp(self, fp: BinaryIO) -> None: ...


GTTSFactory = Callable[..., _GTTSWriter]
Sleep = Callable[[float], Awaitable[None]]


class TTSSynthesisError(RuntimeError):
    """1 音声アセットの gTTS 合成が全試行で失敗した。"""


class GTTSTTS:
    """呼び出しごとに独立して再試行する gTTS アダプター。"""

    def __init__(
        self,
        *,
        retry_count: int = GTTS_RETRY_COUNT,
        tts_factory: GTTSFactory | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if retry_count < 0:
            raise ValueError("retry_count は 0 以上にしてください")
        self.retry_count = retry_count
        self._tts_factory = tts_factory or gTTS
        self._sleep = sleep

    async def synthesize(self, text: str, lang: str) -> Audio:
        normalized_text = text.strip()
        normalized_lang = lang.strip()
        if not normalized_text:
            raise ValueError("text は空にできません")
        if not normalized_lang:
            raise ValueError("lang は空にできません")

        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                mp3_bytes = await asyncio.to_thread(
                    self._synthesize_once,
                    normalized_text,
                    normalized_lang,
                )
                duration_s = mp3_duration_s(mp3_bytes)
                return Audio(bytes=mp3_bytes, mime=MP3_MIME, duration_s=duration_s)
            except Exception as exc:  # noqa: BLE001 - 外部ライブラリ例外を境界で統一
                last_error = exc
                if attempt >= self.retry_count:
                    break
                await self._sleep(_backoff_seconds(attempt))

        raise TTSSynthesisError(
            f"gTTS 音声合成が {self.retry_count + 1} 回すべて失敗しました"
        ) from last_error

    def _synthesize_once(self, text: str, lang: str) -> bytes:
        output = io.BytesIO()
        self._tts_factory(text=text, lang=lang).write_to_fp(output)
        value = output.getvalue()
        if not value:
            raise ValueError("gTTS が空の MP3 を返しました")
        return value


def mp3_duration_s(mp3_bytes: bytes) -> float:
    """mutagen で MP3 ヘッダを解析し、再生時間を秒で返す。"""

    if not mp3_bytes:
        raise ValueError("MP3 は空にできません")
    audio = MP3(io.BytesIO(mp3_bytes))
    duration = float(audio.info.length)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("MP3 の再生時間を取得できませんでした")
    return duration


def _backoff_seconds(attempt: int) -> float:
    if attempt < len(GTTS_BACKOFF_SECONDS):
        return GTTS_BACKOFF_SECONDS[attempt]
    return float(2**attempt)
