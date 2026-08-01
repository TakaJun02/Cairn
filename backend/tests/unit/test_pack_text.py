"""パック用原稿の単一テンプレート、検証、雨天代替選定を検証する。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from app.domains.narration.pack_text import (
    FORBIDDEN_EXPRESSIONS,
    PACK_TEXT_TEMPLATE,
    ItineraryPosition,
    NarrationRole,
    NarrationTravelTime,
    NarrationVariant,
    PackNarrationRequest,
    PackTextGenerator,
    RainAlternative,
    RainCandidate,
    SpotNarrationSource,
    build_pack_text_messages,
    select_rain_alternative,
    validate_narration_text,
)


def _source() -> SpotNarrationSource:
    return SpotNarrationSource(
        spot_id="spot_001",
        name_ja="あがりこ大王",
        social_proof_ja="豪雪が生んだブナの巨木です。",
        tags_ja=("自然", "天然記念物"),
        knowledge_body="幹回りが太いブナで、周囲には遊歩道があります。",
        visit_difficulty="short_walk",
        season_closed_months=(12, 1, 2, 3, 4),
        weather_fit="rain_poor",
        open_hours=None,
    )


def _request(
    *,
    role: NarrationRole = NarrationRole.VISIT,
    variant: NarrationVariant = NarrationVariant.BASE,
) -> PackNarrationRequest:
    return PackNarrationRequest(
        spot_id="spot_001",
        role=role,
        variant=variant,
        pack_spot_ids=("spot_001", "spot_002"),
        itinerary_position=ItineraryPosition(day=1, position=3, next_spot_name="元滝伏流水"),
        month=8,
    )


def test_empty_narration_is_rejected() -> None:
    checked = validate_narration_text(
        " \n ",
        role=NarrationRole.VISIT,
        variant=NarrationVariant.BASE,
    )

    assert checked.accepted is False
    assert checked.reason == "原稿が空です"


@pytest.mark.parametrize("prefix", ["申し訳", "できません"])
def test_short_refusal_is_rejected(prefix: str) -> None:
    checked = validate_narration_text(
        f"{prefix}、ご案内できません。",
        role=NarrationRole.VISIT,
        variant=NarrationVariant.BASE,
    )

    assert checked.accepted is False
    assert checked.reason == "短い拒否応答です"


def test_out_of_range_narration_is_rejected() -> None:
    checked = validate_narration_text(
        "短い原稿です。",
        role=NarrationRole.PASS_BY,
        variant=NarrationVariant.BASE,
    )

    assert checked.accepted is False
    assert "60〜240" in (checked.reason or "")


@pytest.mark.parametrize("phrase", FORBIDDEN_EXPRESSIONS)
def test_forbidden_expression_is_rejected(phrase: str) -> None:
    checked = validate_narration_text(
        phrase + "あ" * 130,
        role=NarrationRole.VISIT,
        variant=NarrationVariant.BASE,
    )

    assert checked.accepted is False
    assert "禁止表現" in (checked.reason or "")


def test_markdown_and_list_markers_are_removed_and_accepted() -> None:
    checked = validate_narration_text(
        "## **雨の日の案内**\n- [足元](https://example.test)に注意してください。"
        "安全な場所で景色を楽しみましょう。",
        role=NarrationRole.VISIT,
        variant=NarrationVariant.WEATHER_RAIN,
    )

    assert checked.accepted is True
    assert checked.text == "雨の日の案内 足元に注意してください。安全な場所で景色を楽しみましょう。"
    assert all(marker not in checked.text for marker in ("#", "*", "-", "[", "]"))


@pytest.mark.parametrize(
    ("role", "variant", "minimum", "maximum"),
    [
        (NarrationRole.VISIT, NarrationVariant.BASE, 120, 420),
        (NarrationRole.PASS_BY, NarrationVariant.BASE, 60, 240),
        (NarrationRole.VISIT, NarrationVariant.WEATHER_CLOUDY, 20, 140),
        (NarrationRole.VISIT, NarrationVariant.WEATHER_RAIN, 20, 140),
        (NarrationRole.VISIT, NarrationVariant.CONGESTION_MID, 20, 140),
        (NarrationRole.VISIT, NarrationVariant.CONGESTION_HIGH, 20, 140),
    ],
)
def test_validation_length_boundaries_are_inclusive(
    role: NarrationRole,
    variant: NarrationVariant,
    minimum: int,
    maximum: int,
) -> None:
    assert validate_narration_text("あ" * minimum, role=role, variant=variant).accepted
    assert validate_narration_text("あ" * maximum, role=role, variant=variant).accepted
    assert not validate_narration_text("あ" * (minimum - 1), role=role, variant=variant).accepted
    assert not validate_narration_text("あ" * (maximum + 1), role=role, variant=variant).accepted


def test_all_variants_render_from_one_template_with_knowledge_context() -> None:
    assert PACK_TEXT_TEMPLATE.count("【地点に直接紐づく知識本文】") == 1
    prompts: list[str] = []
    for variant in NarrationVariant:
        prompt = build_pack_text_messages(_source(), _request(variant=variant))[0]["content"]
        prompts.append(prompt)
        assert _source().knowledge_body in prompt
        assert f"- variant: {variant.value}" in prompt
        if variant is not NarrationVariant.BASE:
            assert "直前にbaseが再生済み" in prompt
    pass_by_prompt = build_pack_text_messages(
        _source(),
        _request(role=NarrationRole.PASS_BY),
    )[0]["content"]
    assert _source().knowledge_body in pass_by_prompt
    assert all(prompt.startswith("あなたは鳥海山周辺") for prompt in [*prompts, pass_by_prompt])


def test_rain_alternative_uses_nearest_eligible_pack_spot() -> None:
    spots = [
        RainCandidate("spot_001", "現在地", "rain_poor"),
        RainCandidate("spot_002", "距離が近い候補", "rain_ok"),
        RainCandidate("spot_003", "時間が短い候補", "rain_ok"),
        RainCandidate("spot_004", "雨に弱い候補", "rain_poor"),
    ]
    travel_times = [
        NarrationTravelTime("spot_001", "spot_002", "car", 1100, 5000),
        NarrationTravelTime("spot_001", "spot_003", "car", 600, 7000),
        NarrationTravelTime("spot_001", "spot_004", "car", 300, 1000),
        NarrationTravelTime("spot_001", "spot_002", "foot", 100, 500),
    ]

    selected = select_rain_alternative(
        from_spot_id="spot_001",
        pack_spots=spots,
        travel_times=travel_times,
    )

    assert selected == RainAlternative(
        spot_id="spot_002",
        name_ja="距離が近い候補",
        travel_min=19,
        distance_m=5000,
    )


class MemoryRepository:
    def __init__(self) -> None:
        self.rain_calls = 0

    async def load_source(self, spot_id: str) -> SpotNarrationSource:
        assert spot_id == "spot_001"
        return _source()

    async def find_rain_alternative(
        self,
        from_spot_id: str,
        pack_spot_ids: Sequence[str],
    ) -> RainAlternative | None:
        self.rain_calls += 1
        assert from_spot_id == "spot_001"
        assert tuple(pack_spot_ids) == ("spot_001", "spot_002")
        return RainAlternative("spot_002", "雨に強い場所", 12, 4000)


class ScriptedGenerationClient:
    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = list(outputs)
        self.messages: list[list[dict[str, str]]] = []

    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        del kwargs
        self.messages.append(list(messages))
        return self.outputs.pop(0)


async def test_invalid_generation_is_retried_once_then_succeeds() -> None:
    repository = MemoryRepository()
    client = ScriptedGenerationClient(
        ["短い", "雨に強い場所へ切り替える選択肢もあります。足元にご注意ください。"]
    )
    generator = PackTextGenerator(repository, client=client)

    result = await generator.generate(_request(variant=NarrationVariant.WEATHER_RAIN))

    assert result.narration_state == "ok"
    assert result.audio_state == "pending"
    assert result.attempts == 2
    assert repository.rain_calls == 1
    assert "雨に強い場所（車で約12分）" in client.messages[0][0]["content"]
    assert "前回は「文字数" in client.messages[1][0]["content"]


async def test_rain_alternative_omission_is_retried_once() -> None:
    repository = MemoryRepository()
    client = ScriptedGenerationClient(
        [
            "雨で足元が滑りやすいため、ゆっくり安全にお進みください。",
            "雨で足元が滑りやすくなっています。車で約12分の雨に強い場所も選べます。",
        ]
    )
    generator = PackTextGenerator(repository, client=client)

    result = await generator.generate(_request(variant=NarrationVariant.WEATHER_RAIN))

    assert result.narration_state == "ok"
    assert result.attempts == 2
    assert result.text is not None and "雨に強い場所" in result.text
    assert "代替候補「雨に強い場所」が含まれていません" in client.messages[1][0]["content"]


async def test_second_invalid_generation_skips_audio() -> None:
    repository = MemoryRepository()
    client = ScriptedGenerationClient(["短い", "まだ短い"])
    generator = PackTextGenerator(repository, client=client)

    result = await generator.generate(_request(variant=NarrationVariant.WEATHER_CLOUDY))

    assert result.text is None
    assert result.narration_state == "failed"
    assert result.audio_state == "skipped"
    assert result.attempts == 2
    assert repository.rain_calls == 0
