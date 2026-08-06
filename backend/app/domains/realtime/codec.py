"""LoRaWAN の 1 byte realtime code と request/response codec。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

PROTOCOL_VERSION = 0x01
UNKNOWN_CODE = 0x0F
UNKNOWN_SPOT_INDEX = 0xFF
MAX_DOWNLINK_CODES = 49  # AS923 DR0〜DR2 の 51 byte - header 2 byte

_KNOWN_VALUES = frozenset({0, 1, 2})


@dataclass(frozen=True, slots=True)
class Uplink:
    """端末が送るパック世代と現在地点（現在地点はログ用途のみ）。"""

    pack_epoch: int
    cur_idx: int


@dataclass(frozen=True, slots=True)
class RealtimeCode:
    """1 スポットの天気・混雑。None は 0xF（不明）に対応する。"""

    weather: int | None
    congestion: int | None


@dataclass(frozen=True, slots=True)
class Downlink:
    """サーバーのパック世代と manifest 順の realtime code 列。"""

    pack_epoch: int
    codes: tuple[RealtimeCode, ...]


def encode_uplink(pack_epoch: int, cur_idx: int = UNKNOWN_SPOT_INDEX) -> bytes:
    """アップリンクを常に 3 byte でエンコードする。"""

    return bytes((PROTOCOL_VERSION, _byte(pack_epoch, "pack_epoch"), _byte(cur_idx, "cur_idx")))


def decode_uplink(payload: bytes) -> Uplink | None:
    """アップリンクを検証する。未知版は例外にせず捨てるため None を返す。"""

    if not payload:
        raise ValueError("uplink payload が空です")
    if payload[0] != PROTOCOL_VERSION:
        return None
    if len(payload) != 3:
        raise ValueError("protocol v1 の uplink は 3 byte 必須です")
    return Uplink(pack_epoch=payload[1], cur_idx=payload[2])


def encode_realtime_code(weather: int | None, congestion: int | None) -> int:
    """天気を上位 4 bit、混雑を下位 4 bit に詰める。"""

    return (_nibble(weather, "weather") << 4) | _nibble(congestion, "congestion")


def decode_realtime_code(value: int) -> RealtimeCode:
    """予約値 3〜14 を拒否し、利用可能な値だけを返す。"""

    encoded = _byte(value, "realtime code")
    weather = _decode_nibble(encoded >> 4, "weather")
    congestion = _decode_nibble(encoded & 0x0F, "congestion")
    return RealtimeCode(weather=weather, congestion=congestion)


def encode_downlink(
    pack_epoch: int,
    codes: Iterable[RealtimeCode | tuple[int | None, int | None]],
) -> bytes:
    """manifest.spots と同じ順のコード列を 2 + N byte で返す。"""

    encoded_codes: list[int] = []
    for code in codes:
        if isinstance(code, RealtimeCode):
            weather, congestion = code.weather, code.congestion
        else:
            weather, congestion = code
        encoded_codes.append(encode_realtime_code(weather, congestion))
    if len(encoded_codes) > MAX_DOWNLINK_CODES:
        raise ValueError(f"downlink code は {MAX_DOWNLINK_CODES} 件以下にしてください")
    return bytes((PROTOCOL_VERSION, _byte(pack_epoch, "pack_epoch"), *encoded_codes))


def decode_downlink(payload: bytes) -> Downlink | None:
    """ダウンリンクを検証する。未知版は例外にせず None を返す。"""

    if not payload:
        raise ValueError("downlink payload が空です")
    if payload[0] != PROTOCOL_VERSION:
        return None
    if len(payload) < 2:
        raise ValueError("protocol v1 の downlink は 2 byte 以上必要です")
    if len(payload) > 2 + MAX_DOWNLINK_CODES:
        raise ValueError("downlink payload が 51 byte を超えています")
    return Downlink(
        pack_epoch=payload[1],
        codes=tuple(decode_realtime_code(value) for value in payload[2:]),
    )


def _byte(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} は 0..255 の整数にしてください")
    return value


def _nibble(value: int | None, name: str) -> int:
    if value is None:
        return UNKNOWN_CODE
    if isinstance(value, bool) or value not in _KNOWN_VALUES:
        raise ValueError(f"{name} は 0, 1, 2, None のいずれかにしてください")
    return value


def _decode_nibble(value: int, name: str) -> int | None:
    if value == UNKNOWN_CODE:
        return None
    if value not in _KNOWN_VALUES:
        raise ValueError(f"{name} に予約値 {value} が含まれています")
    return value
