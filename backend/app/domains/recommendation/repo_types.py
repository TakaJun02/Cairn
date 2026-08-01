"""DB ドライバを import せず使える推薦 repository の値型。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.recommendation.types import PreferenceKey


@dataclass(frozen=True, slots=True)
class RecommendationSpot:
    spot_id: str
    name_ja: str
    description_ja: str | None
    address_ja: str | None
    tags_ja: tuple[str, ...]
    stay_min: int
    weather_fit: str
    visit_difficulty: str
    season_closed_months: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RealtimeValue:
    weather: int | None = None
    congestion: int | None = None


@dataclass(frozen=True, slots=True)
class RecommendationData:
    spots: tuple[RecommendationSpot, ...]
    tag_to_preference: dict[str, PreferenceKey | None]
    realtime: dict[str, RealtimeValue]
    travel_minutes: dict[str, int]
    origin_name_ja: str | None = None

    @property
    def spot_ids(self) -> frozenset[str]:
        return frozenset(spot.spot_id for spot in self.spots)
