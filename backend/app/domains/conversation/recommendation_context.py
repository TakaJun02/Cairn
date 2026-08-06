"""`RecommendationContext` の組み立て(旧 executor.build_recommendation_context)。

`recommend` Tool(§4)と旅程計画サブエージェント(§5 フロー2〜3。効用スコアの
算出に使う)の両方が必要とするため、`main_agent.py`/`itinerary_subagent.py`
のどちらからも import できる中立モジュールに置く(相互 import を避ける)。
"""

from __future__ import annotations

from datetime import date

from app.domains.conversation.state import TurnState
from app.domains.recommendation.types import RecommendationContext, RecommendationProfile


def build_recommendation_context(state: TurnState) -> RecommendationContext:
    itinerary = state.itinerary.itinerary if state.itinerary is not None else None
    day_dates: dict[int, date] = {}
    day_previous: dict[int, str] = {}
    day_origins: dict[int, str] = {}
    previous_spot_id: str | None = None
    base_spot_id: str | None = state.default_origin_spot_id
    travel_date: date | None = None
    if itinerary is not None:
        for index, day in enumerate(itinerary.days, 1):
            try:
                parsed_date = date.fromisoformat(day.date)
            except ValueError:
                continue
            day_dates[index] = parsed_date
            travel_date = travel_date or parsed_date
            day_origins[index] = day.origin.spot_id
            if day.items:
                day_previous[index] = day.items[-1].spot_id
                previous_spot_id = day.items[-1].spot_id
            base_spot_id = base_spot_id or day.origin.spot_id
    profile_values = state.profile.model_dump(mode="python")
    if state.profile_delta is not None:
        delta = state.profile_delta
        interests = dict(profile_values["interests"])
        interests.update({key.value: value for key, value in delta.interests.items()})
        profile_values.update(
            {
                "interests": interests,
                "party": delta.party.value if delta.party is not None else None,
                "mobility": (delta.mobility.value if delta.mobility is not None else None),
                "pace": delta.pace.value if delta.pace is not None else None,
                "avoid": list(dict.fromkeys([*profile_values["avoid"], *delta.avoid])),
                "notes": delta.notes,
            }
        )
        for field_name in ("party", "mobility", "pace", "notes"):
            if profile_values[field_name] is None:
                profile_values[field_name] = getattr(state.profile, field_name)
    profile = RecommendationProfile.model_validate(profile_values)
    return RecommendationContext(
        profile=profile,
        presented_spot_ids=state.presented_spot_ids,
        previous_spot_id=previous_spot_id,
        base_spot_id=base_spot_id,
        travel_date=travel_date,
        day_dates=day_dates,
        day_previous_spot_ids=day_previous,
        day_origin_spot_ids=day_origins,
        score_adjustments={value.spot_id: value.delta for value in state.score_adjustments},
    )
