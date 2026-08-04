"""LoRaWAN realtime codec の byte 契約。"""

import pytest

from app.domains.realtime.codec import (
    Downlink,
    RealtimeCode,
    Uplink,
    decode_downlink,
    decode_realtime_code,
    decode_uplink,
    encode_downlink,
    encode_realtime_code,
    encode_uplink,
)


def test_uplink_round_trip_is_exactly_three_bytes() -> None:
    payload = encode_uplink(pack_epoch=7, cur_idx=0xFF)

    assert payload == bytes((0x01, 7, 0xFF))
    assert decode_uplink(payload) == Uplink(pack_epoch=7, cur_idx=0xFF)


def test_downlink_round_trip_preserves_unknown_and_20_spots_fit() -> None:
    codes = tuple(
        RealtimeCode(
            weather=None if index % 4 == 0 else index % 3,
            congestion=None if index % 5 == 0 else (index + 1) % 3,
        )
        for index in range(20)
    )

    payload = encode_downlink(255, codes)

    assert len(payload) == 22
    assert decode_downlink(payload) == Downlink(pack_epoch=255, codes=codes)


def test_unknown_is_f_and_never_zero_filled() -> None:
    assert encode_realtime_code(None, None) == 0xFF
    assert encode_realtime_code(None, 2) == 0xF2
    assert encode_realtime_code(1, None) == 0x1F
    assert decode_realtime_code(0xFF) == RealtimeCode(None, None)


@pytest.mark.parametrize("payload", [b"\x02", b"\x02\x07", b"\x02\x07\xff"])
def test_unknown_versions_are_discarded_without_exception(payload: bytes) -> None:
    assert decode_uplink(payload) is None
    assert decode_downlink(payload) is None


@pytest.mark.parametrize("value", [3, 4, 14])
def test_reserved_values_are_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        encode_realtime_code(value, 0)
    with pytest.raises(ValueError):
        decode_realtime_code(value << 4)


def test_epoch_mismatch_response_can_contain_no_codes() -> None:
    assert encode_downlink(8, ()) == b"\x01\x08"
    assert decode_downlink(b"\x01\x08") == Downlink(pack_epoch=8, codes=())
