"""音声値型と MP3 長さ取得の単体テスト。"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

from app.domains.voice import gtts_impl


def test_mp3_duration_is_read_by_mutagen_from_in_memory_header(monkeypatch: Any) -> None:
    seen: list[bytes] = []

    def fake_mp3(file_object: io.BytesIO) -> SimpleNamespace:
        seen.append(file_object.read())
        return SimpleNamespace(info=SimpleNamespace(length=12.75))

    monkeypatch.setattr(gtts_impl, "MP3", fake_mp3)

    assert gtts_impl.mp3_duration_s(b"mp3-header-and-frames") == 12.75
    assert seen == [b"mp3-header-and-frames"]
