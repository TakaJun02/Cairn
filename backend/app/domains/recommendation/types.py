"""`agent_planning_phase.md` §18.2〜§18.3 に対応する推薦ドメイン型。"""

from __future__ import annotations

import math
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainModel(BaseModel):
    """Tool 内部契約へ未定義の値を混ぜない。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PreferenceKey(StrEnum):
    NATURE = "nature"
    MOUNTAIN = "mountain"
    WATER = "water"
    LODGING = "lodging"
    SHRINE_TEMPLE = "shrine_temple"
    ONSEN = "onsen"
    PARK = "park"
    COAST = "coast"
    HISTORY = "history"
    FOOD = "food"
    REST_STOP = "rest_stop"
    FAMILY = "family"


class Mobility(StrEnum):
    AVOID_WALK = "avoid_walk"
    SHORT_WALK_OK = "short_walk_ok"
    HIKE_OK = "hike_ok"


class Party(StrEnum):
    FAMILY_KIDS = "family_kids"
    COUPLE = "couple"
    SOLO = "solo"
    SENIOR = "senior"
    GROUP = "group"


class Pace(StrEnum):
    PACKED = "packed"
    RELAXED = "relaxed"


WeatherFitToday = Literal["good", "ok", "poor", "unknown"]
CongestionLevel = Literal["low", "mid", "high", "unknown"]
CandidateStage = Literal["provisional", "final"]


class RecommendFilter(DomainModel):
    tags: list[str] = Field(default_factory=list)
    mobility: Mobility | None = None
    weather_fit: bool | None = None
    area: str | None = None
    day: int | None = Field(default=None, ge=1)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value:
                raise ValueError("tags に空文字は指定できません")
            if value not in result:
                result.append(value)
        return result

    @field_validator("area")
    @classmethod
    def normalize_area(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("area に空文字は指定できません")
        return normalized


class RecommendRequest(DomainModel):
    filter: RecommendFilter = Field(default_factory=RecommendFilter)
    k: int = Field(default=5, ge=1, le=8)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("exclude")
    @classmethod
    def deduplicate_excludes(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class RejectedSpot(DomainModel):
    id: str
    reason: str = ""


class RecommendationProfile(DomainModel):
    interests: dict[PreferenceKey, float] = Field(default_factory=dict)
    party: Party | None = None
    mobility: Mobility | None = None
    pace: Pace | None = None
    avoid: list[str] = Field(default_factory=list)
    liked_spots: list[str] = Field(default_factory=list)
    rejected_spots: list[RejectedSpot] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("interests")
    @classmethod
    def validate_interests(
        cls, values: dict[PreferenceKey, float]
    ) -> dict[PreferenceKey, float]:
        for key, value in values.items():
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"interests.{key.value} は -1.0〜1.0 にしてください")
        return values


class RecommendationContext(DomainModel):
    """conversation が DB 状態から組み立てて Tool へ渡す暗黙文脈。"""

    profile: RecommendationProfile = Field(default_factory=RecommendationProfile)
    presented_spot_ids: list[str] = Field(default_factory=list)
    previous_spot_id: str | None = None
    base_spot_id: str | None = None
    travel_date: date | None = None
    day_dates: dict[int, date] = Field(default_factory=dict)
    day_previous_spot_ids: dict[int, str] = Field(default_factory=dict)
    day_origin_spot_ids: dict[int, str] = Field(default_factory=dict)

    def origin_spot_id(self, day: int | None) -> str | None:
        """旅程の直前地点、拠点の順で travel_times の起点を決める。"""

        if day is not None:
            return (
                self.day_previous_spot_ids.get(day)
                or self.day_origin_spot_ids.get(day)
                or self.base_spot_id
            )
        return self.previous_spot_id or self.base_spot_id

    def target_date(self, day: int | None, *, today: date) -> date:
        if day is not None and day in self.day_dates:
            return self.day_dates[day]
        return self.travel_date or today


class ReasonMaterials(DomainModel):
    matched_keys: list[PreferenceKey] = Field(default_factory=list)
    matched_tags: list[str] = Field(default_factory=list)
    travel_time_text: str = ""
    weather_fit_today: WeatherFitToday = "unknown"
    congestion: CongestionLevel = "unknown"
    stay_min: int = Field(ge=0)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class RecommendationCandidate(DomainModel):
    spot_id: str
    rank: int = Field(ge=1)
    reason_materials: ReasonMaterials


class RecommendationResult(DomainModel):
    spot_ids: list[str]
    candidates: list[RecommendationCandidate]
    provisional_spot_ids: list[str]
    rerank_used: bool


class CandidateStateEvent(DomainModel):
    """SSE へ変換する前の provisional/final 差し込み口。"""

    stage: CandidateStage
    spot_ids: list[str]
    candidates: list[RecommendationCandidate]
