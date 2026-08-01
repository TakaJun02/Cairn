"""ハードフィルタと、タグ語彙に接地した決定的スコアリング。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.recommendation.repo_types import (
    RealtimeValue,
    RecommendationData,
    RecommendationSpot,
)
from app.domains.recommendation.types import (
    CongestionLevel,
    Mobility,
    Pace,
    PreferenceKey,
    ReasonMaterials,
    RecommendRequest,
    RecommendationContext,
    WeatherFitToday,
)

_MOBILITY_DIFFICULTIES: dict[Mobility, frozenset[str]] = {
    Mobility.AVOID_WALK: frozenset({"no_walk"}),
    Mobility.SHORT_WALK_OK: frozenset({"no_walk", "short_walk", "long_walk"}),
    Mobility.HIKE_OK: frozenset({"no_walk", "short_walk", "long_walk", "hike"}),
}
_RAIN_WEATHER_SCORE = {
    "indoor": 1.5,
    "rain_ok": 1.0,
    "rain_fair": 0.25,
    "rain_poor": -2.0,
    # rain_unsafe はこの値を見る前に必ず pre-filter される。
    "rain_unsafe": -100.0,
}
_CONGESTION_SCORE = {0: 0.3, 1: -0.4, 2: -1.25}
_CONGESTION_LABEL: dict[int, CongestionLevel] = {0: "low", 1: "mid", 2: "high"}


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    preference: float = 2.0
    presented_penalty: float = -8.0
    rejected_penalty: float = -12.0
    liked_boost: float = 1.5
    travel_penalty_per_min: float = -0.03
    travel_penalty_cap_min: int = 80
    packed_short_stay_boost: float = 0.4
    relaxed_long_stay_boost: float = 0.3


DEFAULT_WEIGHTS = ScoringWeights()
INELIGIBLE_ITINERARY_UTILITY = -1_000.0


@dataclass(frozen=True, slots=True)
class ScoredSpot:
    spot: RecommendationSpot
    score: float
    reason_materials: ReasonMaterials


def fold_tags(
    tags: tuple[str, ...] | list[str],
    tag_to_preference: dict[str, PreferenceKey | None],
) -> tuple[PreferenceKey, ...]:
    """生タグを DB 対応表で 12 選好キーの二値ベクトルへ畳む。"""

    found = {
        preference
        for tag in tags
        if (preference := tag_to_preference.get(tag)) is not None
    }
    return tuple(key for key in PreferenceKey if key in found)


def score_catalog(
    data: RecommendationData,
    request: RecommendRequest,
    context: RecommendationContext,
    *,
    target_month: int,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> list[ScoredSpot]:
    """eligible な全地点を score 降順、同点時 spot_id 順で返す。"""

    result: list[ScoredSpot] = []
    for spot in data.spots:
        realtime = data.realtime.get(spot.spot_id, RealtimeValue())
        rainy = request.filter.weather_fit is True or realtime.weather == 2
        if not _is_eligible(
            spot,
            request,
            context,
            target_month=target_month,
            rainy=rainy,
        ):
            continue
        result.append(_score_spot(spot, data, request, context, realtime, rainy, weights))
    return sorted(result, key=lambda value: (-value.score, value.spot.spot_id))


def to_itinerary_utilities(
    data: RecommendationData,
    scored: list[ScoredSpot],
) -> dict[str, float]:
    """ItineraryService.utilities へそのまま渡せる全地点の効用表を作る。"""

    utilities = {
        spot.spot_id: INELIGIBLE_ITINERARY_UTILITY for spot in data.spots
    }
    for value in scored:
        # default_utility=1.0 を中立点として、推薦スコアをその上へ接続する。
        utilities[value.spot.spot_id] = round(1.0 + value.score, 6)
    return utilities


def _is_eligible(
    spot: RecommendationSpot,
    request: RecommendRequest,
    context: RecommendationContext,
    *,
    target_month: int,
    rainy: bool,
) -> bool:
    if spot.spot_id in set(request.exclude):
        return False
    if target_month in spot.season_closed_months:
        return False
    if rainy and spot.weather_fit == "rain_unsafe":
        return False

    mobility = request.filter.mobility or context.profile.mobility
    if mobility is not None and spot.visit_difficulty not in _MOBILITY_DIFFICULTIES[mobility]:
        return False

    requested_tags = set(request.filter.tags)
    if requested_tags and requested_tags.isdisjoint(spot.tags_ja):
        # 複数タグは「いずれか」。
        # 全件 AND にすると小カタログでは空になりやすい。
        return False

    area = request.filter.area
    if area is not None and (
        spot.address_ja is None
        or area.casefold() not in spot.address_ja.casefold()
    ):
        return False
    return True


def _score_spot(
    spot: RecommendationSpot,
    data: RecommendationData,
    request: RecommendRequest,
    context: RecommendationContext,
    realtime: RealtimeValue,
    rainy: bool,
    weights: ScoringWeights,
) -> ScoredSpot:
    preference_keys = fold_tags(spot.tags_ja, data.tag_to_preference)
    interests = context.profile.interests
    preference = weights.preference * sum(interests.get(key, 0.0) for key in preference_keys)
    weather = _RAIN_WEATHER_SCORE.get(spot.weather_fit, 0.0) if rainy else 0.0
    congestion = _CONGESTION_SCORE.get(realtime.congestion, 0.0)

    travel_min = data.travel_minutes.get(spot.spot_id)
    travel = (
        weights.travel_penalty_per_min * min(travel_min, weights.travel_penalty_cap_min)
        if travel_min is not None
        else 0.0
    )
    liked = weights.liked_boost if spot.spot_id in context.profile.liked_spots else 0.0
    rejected_ids = {value.id for value in context.profile.rejected_spots}
    rejected = weights.rejected_penalty if spot.spot_id in rejected_ids else 0.0
    presented = (
        weights.presented_penalty if spot.spot_id in context.presented_spot_ids else 0.0
    )
    pace = _pace_score(spot, context.profile.pace, weights)

    components = {
        "preference": preference,
        "weather": weather,
        "congestion": congestion,
        "travel_time": travel,
        "pace": pace,
        "liked": liked,
        "rejected": rejected,
        "presented": presented,
    }
    total = sum(components.values())
    breakdown = {key: round(value, 6) for key, value in components.items()}
    breakdown["total"] = round(total, 6)

    matched_key_set = {key for key in preference_keys if interests.get(key, 0.0) > 0}
    requested_tags = set(request.filter.tags)
    matched_tags = [
        tag
        for tag in spot.tags_ja
        if data.tag_to_preference.get(tag) in matched_key_set or tag in requested_tags
    ]
    materials = ReasonMaterials(
        matched_keys=[key for key in PreferenceKey if key in matched_key_set],
        matched_tags=matched_tags,
        travel_time_text=_travel_time_text(data.origin_name_ja, travel_min),
        weather_fit_today=_weather_fit_today(spot.weather_fit, realtime.weather),
        congestion=_congestion_level(realtime.congestion),
        stay_min=spot.stay_min,
        score_breakdown=breakdown,
    )
    return ScoredSpot(spot=spot, score=round(total, 6), reason_materials=materials)


def _pace_score(
    spot: RecommendationSpot,
    pace: Pace | None,
    weights: ScoringWeights,
) -> float:
    if pace is Pace.PACKED and spot.stay_min <= 45:
        return weights.packed_short_stay_boost
    if pace is Pace.RELAXED and spot.stay_min >= 60:
        return weights.relaxed_long_stay_boost
    return 0.0


def _weather_fit_today(static_fit: str, weather: int | None) -> WeatherFitToday:
    if weather not in {0, 1, 2}:
        return "unknown"
    if weather in {0, 1}:
        return "good"
    return {
        "indoor": "good",
        "rain_ok": "good",
        "rain_fair": "ok",
        "rain_poor": "poor",
        "rain_unsafe": "poor",
    }.get(static_fit, "unknown")  # type: ignore[return-value]


def _congestion_level(congestion: int | None) -> CongestionLevel:
    return _CONGESTION_LABEL.get(congestion, "unknown")


def _travel_time_text(origin_name_ja: str | None, minutes: int | None) -> str:
    if origin_name_ja is None or minutes is None:
        return ""
    if minutes == 0:
        return f"{origin_name_ja}と同じ地点（車移動なし）"
    rounded = max(5, ((minutes + 2) // 5) * 5)
    return f"{origin_name_ja}から車で約 {rounded} 分"
