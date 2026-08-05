"""旅程ダイジェスト整形器のマスク処理を検証する([25 §1-7])。

譲歩文(`Concession.message_ja`)の唯一の生成点は `predicates.py` であり、
そこでは表示名で文を組む(`test_itinerary_predicates.py` が検証)。ここでは
**旧形式(spot_id 入り)で永続化済みの版**への防御として、ダイジェスト整形器が
`message_ja` 中の `spot_` トークンをマスクすることと、名前解決フォールバックが
spot_id を返さないことを検証する。
"""

from __future__ import annotations

from app.domains.conversation.itinerary_digest import (
    UNNAMED_SPOT_JA,
    _name,
    format_itinerary_digest,
    mask_concession_list,
    mask_spot_ids,
)
from app.domains.itinerary.types import (
    Concession,
    Itinerary,
    ItineraryDay,
    PredEnum,
    SpotEndpoint,
)


def _legacy_concession(spot_id: str) -> Concession:
    return Concession(
        constraint_id="c_001",
        pred=PredEnum.REQUIRE,
        args={"target": spot_id},
        violation=1.0,
        message_ja=f"必須希望の「{spot_id}」を旅程に入れられませんでした",
    )


def _one_day_itinerary(concessions: list[Concession]) -> Itinerary:
    return Itinerary(
        days=[
            ItineraryDay(
                date="2026-08-10",
                start_min=540,
                end_min=1020,
                origin=SpotEndpoint(spot_id="spot_origin"),
                destination=SpotEndpoint(spot_id="spot_origin"),
                items=[],
            )
        ],
        version=1,
        concessions=concessions,
    )


def test_mask_spot_ids_replaces_known_id_with_display_name() -> None:
    text = "必須希望の「spot_014」を旅程に入れられませんでした"
    masked = mask_spot_ids(text, {"spot_014": "元滝伏流水"})

    assert masked == "必須希望の「元滝伏流水」を旅程に入れられませんでした"
    assert "spot_014" not in masked


def test_mask_spot_ids_falls_back_to_neutral_label_for_unknown_id() -> None:
    text = "必須希望の「spot_999」を旅程に入れられませんでした"
    masked = mask_spot_ids(text, {})

    assert "spot_999" not in masked
    assert UNNAMED_SPOT_JA in masked


def test_mask_spot_ids_leaves_text_without_spot_id_unchanged() -> None:
    text = "神社の訪問が希望上限を1件超えています"
    assert mask_spot_ids(text, {}) == text


def test_format_itinerary_digest_masks_legacy_spot_id_in_concession_message() -> None:
    itinerary = _one_day_itinerary([_legacy_concession("spot_014")])

    digest = format_itinerary_digest(itinerary, spot_names={"spot_014": "元滝伏流水"})

    assert "元滝伏流水" in digest
    assert "spot_014" not in digest


def test_format_itinerary_digest_uses_neutral_label_when_unresolvable() -> None:
    itinerary = _one_day_itinerary([_legacy_concession("spot_999")])

    digest = format_itinerary_digest(itinerary, spot_names={})

    assert "spot_999" not in digest
    assert UNNAMED_SPOT_JA in digest


def test_name_fallback_does_not_return_spot_id() -> None:
    assert _name({}, "spot_014") == UNNAMED_SPOT_JA
    assert _name({"spot_014": "元滝伏流水"}, "spot_014") == "元滝伏流水"


def test_mask_concession_list_masks_message_ja_only() -> None:
    concessions = [
        {
            "constraint_id": "c_001",
            "pred": "require",
            "args": {"target": "spot_014"},
            "violation": 1.0,
            "message_ja": "必須希望の「spot_014」を旅程に入れられませんでした",
        }
    ]

    masked = mask_concession_list(concessions, {"spot_014": "元滝伏流水"})

    assert masked[0]["message_ja"] == "必須希望の「元滝伏流水」を旅程に入れられませんでした"
    # args はマスク対象外(機械用の値。25 §1-7 の対象は表示用テキストのみ)。
    assert masked[0]["args"] == {"target": "spot_014"}
