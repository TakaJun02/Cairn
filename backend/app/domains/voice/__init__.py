"""音声合成のポートと既定 gTTS 実装。"""

from app.domains.voice.gtts_impl import GTTSTTS, TTSSynthesisError, mp3_duration_s
from app.domains.voice.port import Audio, TTSPort

__all__ = [
    "Audio",
    "GTTSTTS",
    "TTSPort",
    "TTSSynthesisError",
    "mp3_duration_s",
]
