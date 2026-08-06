"""外部 gTTS をモックした再試行・障害分離の契約テスト。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, BinaryIO

import pytest

from app.domains.voice import gtts_impl
from app.domains.voice.gtts_impl import GTTSTTS, TTSSynthesisError


class ScriptedGTTSFactory:
    def __init__(self, outcomes: Sequence[bytes | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, *, text: str, lang: str) -> ScriptedGTTSFactory:
        self.calls.append((text, lang))
        return self

    def write_to_fp(self, fp: BinaryIO) -> None:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        fp.write(outcome)


async def test_gtts_retries_three_times_with_exponential_backoff(
    monkeypatch: Any,
) -> None:
    factory = ScriptedGTTSFactory([RuntimeError("rate limited")] * 3 + [b"valid-mp3"])
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(gtts_impl, "mp3_duration_s", lambda value: 8.5)
    tts = GTTSTTS(tts_factory=factory, sleep=fake_sleep)

    audio = await tts.synthesize("案内です", "ja")

    assert audio.bytes == b"valid-mp3"
    assert audio.mime == "audio/mpeg"
    assert audio.duration_s == 8.5
    assert factory.calls == [("案内です", "ja")] * 4
    assert sleeps == [1.0, 2.0, 4.0]


async def test_failure_is_confined_to_one_asset_without_shared_cooldown(
    monkeypatch: Any,
) -> None:
    factory = ScriptedGTTSFactory([RuntimeError("rate limited")] * 4 + [b"next-asset-mp3"])
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(gtts_impl, "mp3_duration_s", lambda value: 3.25)
    tts = GTTSTTS(tts_factory=factory, sleep=fake_sleep)

    with pytest.raises(TTSSynthesisError):
        await tts.synthesize("失敗するアセット", "ja")
    audio = await tts.synthesize("次のアセット", "ja")

    assert audio.bytes == b"next-asset-mp3"
    assert factory.calls[-1] == ("次のアセット", "ja")
    assert sleeps == [1.0, 2.0, 4.0]
