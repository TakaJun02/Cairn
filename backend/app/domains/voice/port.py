"""音声合成実装をパック生成から隔離するポート。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Audio:
    """永続化前の、合成に成功した音声アセット。"""

    bytes: bytes
    mime: str
    duration_s: float


@runtime_checkable
class TTSPort(Protocol):
    async def synthesize(self, text: str, lang: str) -> Audio: ...
