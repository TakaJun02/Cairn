"""推薦の pre-filter、スコア、縮退、クローズドワールドを検証する。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domains.recommendation.repo_types import (
    RealtimeValue,
    RecommendationData,
    RecommendationSpot,
)
from app.domains.recommendation.rerank import LLMRecommendationReranker
from app.domains.recommendation.scoring import (
    INELIGIBLE_ITINERARY_UTILITY,
    ScoredSpot,
    fold_tags,
    score_catalog,
)
from app.domains.recommendation.service import RecommendationService, recommend
from app.domains.recommendation.types import (
    CandidateStateEvent,
    PreferenceKey,
    RecommendationContext,
    RecommendationProfile,
    RecommendFilter,
    RecommendRequest,
)


def _spot(
    number: int,
    tags: tuple[str, ...],
    *,
    weather_fit: str = "rain_ok",
    difficulty: str = "no_walk",
    closed: tuple[int, ...] = (),
    stay_min: int = 30,
    address: str = "秋田県にかほ市",
) -> RecommendationSpot:
    return RecommendationSpot(
        spot_id=f"spot_{number:03d}",
        name_ja=f"地点{number}",
        description_ja=f"地点{number}のDB説明",
        address_ja=address,
        tags_ja=tags,
        stay_min=stay_min,
        weather_fit=weather_fit,
        visit_difficulty=difficulty,
        season_closed_months=closed,
    )


def _data() -> RecommendationData:
    spots = (
        _spot(1, ("滝", "自然")),
        _spot(2, ("登山",), weather_fit="rain_unsafe", difficulty="hike"),
        _spot(3, ("博物館",), weather_fit="indoor", closed=(8,)),
        _spot(4, ("自然",), weather_fit="rain_poor"),
        _spot(5, ("博物館",), weather_fit="indoor", stay_min=75),
        _spot(6, ("滝",)),
        _spot(7, ("温泉",), weather_fit="indoor"),
        _spot(8, ("自然",), difficulty="short_walk"),
        _spot(9, ("自然",), difficulty="long_walk"),
        _spot(10, ("観光",)),
        _spot(11, ("自然",), address="山形県遊佐町"),
        _spot(12, ("温泉",), weather_fit="indoor", address="山形県酒田市"),
    )
    return RecommendationData(
        spots=spots,
        tag_to_preference={
            "滝": PreferenceKey.WATER,
            "自然": PreferenceKey.NATURE,
            "登山": PreferenceKey.MOUNTAIN,
            "博物館": PreferenceKey.HISTORY,
            "温泉": PreferenceKey.ONSEN,
            "観光": None,
        },
        realtime={
            "spot_001": RealtimeValue(weather=0, congestion=0),
            "spot_005": RealtimeValue(weather=2, congestion=2),
        },
        travel_minutes={},
    )


class MemoryRecommendationRepository:
    def __init__(self, data: RecommendationData | None = None) -> None:
        self.data = data or _data()
        self.existence_checks: list[list[str]] = []
        self.origins: list[str | None] = []

    async def load_data(self, origin_spot_id: str | None = None) -> RecommendationData:
        self.origins.append(origin_spot_id)
        if origin_spot_id is None:
            return self.data
        return replace(
            self.data,
            travel_minutes={spot.spot_id: 14 for spot in self.data.spots},
            origin_name_ja="宿",
        )

    async def existing_spot_ids(self, spot_ids: list[str]) -> set[str]:
        self.existence_checks.append(list(spot_ids))
        return set(spot_ids) & self.data.spot_ids


class StubReranker:
    def __init__(self, result: list[str] | None = None, error: Exception | None = None) -> None:
        self.result = result or []
        self.error = error
        self.seen: list[str] = []

    async def rerank(
        self,
        candidates: list[ScoredSpot],
        *,
        request: RecommendRequest,
        context: RecommendationContext,
        k: int,
    ) -> list[str]:
        self.seen = [value.spot.spot_id for value in candidates]
        if self.error is not None:
            raise self.error
        return self.result


class CaptureGenerationClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.kwargs = {"messages": messages, **kwargs}
        return self.response


def _settings(enabled: bool) -> Settings:
    return Settings(recommendation_rerank_enabled=enabled)


def _context(**kwargs: Any) -> RecommendationContext:
    values: dict[str, Any] = {
        "profile": RecommendationProfile(
            interests={PreferenceKey.WATER: 1.0, PreferenceKey.NATURE: 0.2}
        ),
        "travel_date": date(2026, 8, 2),
    }
    values.update(kwargs)
    return RecommendationContext(**values)


def test_fold_tags_uses_database_vocabulary_and_profile_rejects_raw_tags() -> None:
    data = _data()

    assert fold_tags(("滝", "自然", "観光"), data.tag_to_preference) == (
        PreferenceKey.NATURE,
        PreferenceKey.WATER,
    )
    with pytest.raises(ValidationError):
        RecommendationProfile(interests={"滝": 0.8})  # type: ignore[dict-item]


async def test_tool_contract_has_no_itinerary_precondition_and_defaults_to_five() -> None:
    result = await recommend(
        MemoryRecommendationRepository(),
        settings=_settings(False),
    )

    assert len(result.spot_ids) == 5
    assert result.spot_ids == result.provisional_spot_ids[:5]


async def test_hard_filters_mobility_rain_and_season_but_keep_rain_poor() -> None:
    service = RecommendationService(
        MemoryRecommendationRepository(),
        settings=_settings(False),
        today_provider=lambda: date(2026, 8, 2),
    )

    result = await service.recommend(
        RecommendRequest(
            filter=RecommendFilter(mobility="avoid_walk", weather_fit=True),
            k=8,
        ),
        context=_context(),
    )

    assert "spot_002" not in result.provisional_spot_ids  # hike + rain_unsafe
    assert "spot_003" not in result.provisional_spot_ids  # 8月閉鎖
    assert "spot_004" in result.provisional_spot_ids  # rain_poor は減点のみ
    assert result.spot_ids.index("spot_004") > result.spot_ids.index("spot_001")


async def test_presented_penalty_moves_repeated_spot_from_first_place() -> None:
    service = RecommendationService(
        MemoryRecommendationRepository(),
        settings=_settings(False),
        today_provider=lambda: date(2026, 7, 1),
    )
    request = RecommendRequest(k=2, filter=RecommendFilter(tags=["滝"]))
    first = await service.recommend(
        request,
        context=_context(travel_date=date(2026, 7, 1)),
    )
    repeated = await service.recommend(
        request,
        context=_context(
            travel_date=date(2026, 7, 1),
            presented_spot_ids=[first.spot_ids[0]],
        )
    )

    assert first.spot_ids[0] == "spot_001"
    assert repeated.spot_ids[0] != first.spot_ids[0]
    repeated_materials = next(
        value.reason_materials
        for value in repeated.candidates
        if value.spot_id == first.spot_ids[0]
    )
    assert repeated_materials.score_breakdown["presented"] == -8.0


async def test_llm_failure_degrades_to_deterministic_score_order() -> None:
    repository = MemoryRecommendationRepository()
    reranker = StubReranker(error=RuntimeError("LLM down"))
    service = RecommendationService(
        repository,
        settings=_settings(True),
        reranker=reranker,
        today_provider=lambda: date(2026, 7, 1),
    )

    result = await service.recommend(RecommendRequest(k=5), context=_context())

    assert result.rerank_used is False
    assert result.spot_ids == result.provisional_spot_ids[:5]
    assert reranker.seen == result.provisional_spot_ids


async def test_hallucinated_and_out_of_candidate_ids_are_discarded_by_db_check() -> None:
    repository = MemoryRecommendationRepository()
    reranker = StubReranker(["spot_999", "spot_005", "spot_012", "spot_001"])
    service = RecommendationService(
        repository,
        settings=_settings(True),
        reranker=reranker,
        today_provider=lambda: date(2026, 7, 1),
    )

    result = await service.recommend(RecommendRequest(k=3), context=_context())

    assert result.rerank_used is True
    assert result.spot_ids[:2] == ["spot_005", "spot_001"]
    assert "spot_999" not in result.spot_ids
    assert set(result.spot_ids) <= repository.data.spot_ids
    assert any("spot_999" in checked for checked in repository.existence_checks)


async def test_llm_rerank_uses_score_order_and_candidate_limited_guided_schema() -> None:
    request = RecommendRequest(k=2)
    context = _context(travel_date=date(2026, 7, 1))
    scored = score_catalog(_data(), request, context, target_month=7)[:4]
    client = CaptureGenerationClient('{"spot_ids":["spot_006","spot_001"]}')
    reranker = LLMRecommendationReranker(client)  # type: ignore[arg-type]

    selected = await reranker.rerank(scored, request=request, context=context, k=2)

    assert selected == ["spot_006", "spot_001"]
    response_format = client.kwargs["extra_body"]["response_format"]
    schema = response_format["json_schema"]["schema"]
    assert schema["properties"]["spot_ids"]["items"]["enum"] == [
        value.spot.spot_id for value in scored
    ]
    assert schema["properties"]["spot_ids"]["minItems"] == 2
    assert client.kwargs["temperature"] == 0.0


async def test_rerank_off_is_deterministic_and_emits_provisional_then_final() -> None:
    events: list[CandidateStateEvent] = []
    repository = MemoryRecommendationRepository()
    reranker = StubReranker(["spot_006"])
    service = RecommendationService(
        repository,
        settings=_settings(False),
        reranker=reranker,
        event_sink=events.append,
        today_provider=lambda: date(2026, 7, 1),
    )
    request = RecommendRequest(k=4, filter=RecommendFilter(area="秋田県"))

    first = await service.recommend(request, context=_context())
    second = await service.recommend(request, context=_context())

    assert first == second
    assert [event.stage for event in events] == [
        "provisional",
        "final",
        "provisional",
        "final",
    ]
    assert len(first.provisional_spot_ids) <= 10
    assert first.rerank_used is False
    assert reranker.seen == []


async def test_reason_materials_use_realtime_and_db_travel_time_without_invention() -> None:
    repository = MemoryRecommendationRepository()
    service = RecommendationService(
        repository,
        settings=_settings(False),
        today_provider=lambda: date(2026, 7, 1),
    )

    result = await service.recommend(
        RecommendRequest(k=8),
        context=_context(previous_spot_id="spot_base", travel_date=date(2026, 7, 1)),
    )
    by_id = {value.spot_id: value.reason_materials for value in result.candidates}

    assert repository.origins == ["spot_base"]
    assert by_id["spot_001"].travel_time_text == "宿から車で約 15 分"
    assert by_id["spot_001"].weather_fit_today == "good"
    assert by_id["spot_001"].congestion == "low"
    assert by_id["spot_006"].weather_fit_today == "unknown"
    assert by_id["spot_006"].congestion == "unknown"


async def test_itinerary_utility_adapter_replaces_uniform_default_for_all_spots() -> None:
    repository = MemoryRecommendationRepository()
    service = RecommendationService(
        repository,
        settings=_settings(False),
        today_provider=lambda: date(2026, 8, 2),
    )

    utilities = await service.build_itinerary_utilities(
        RecommendRequest(filter=RecommendFilter(mobility="avoid_walk", weather_fit=True)),
        context=_context(),
    )

    assert set(utilities) == repository.data.spot_ids
    assert utilities["spot_002"] == INELIGIBLE_ITINERARY_UTILITY
    assert utilities["spot_003"] == INELIGIBLE_ITINERARY_UTILITY
    assert utilities["spot_001"] > utilities["spot_004"]
