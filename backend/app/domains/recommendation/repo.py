"""推薦用の静的カタログ、リアルタイム値、移動時間を一括で読む。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Spot, SpotRealtime, TagVocabulary, TravelTime
from app.domains.recommendation.repo_types import (
    RealtimeValue,
    RecommendationData,
    RecommendationSpot,
)
from app.domains.recommendation.types import PreferenceKey


class RecommendationRepository:
    """呼び出し側のトランザクションに参加し、暗黙 commit はしない。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_data(self, origin_spot_id: str | None = None) -> RecommendationData:
        spot_rows = (
            await self.session.execute(
                select(
                    Spot.spot_id,
                    Spot.name_ja,
                    Spot.description,
                    Spot.address,
                    Spot.tags_ja,
                    Spot.stay_min,
                    Spot.weather_fit,
                    Spot.visit_difficulty,
                    Spot.season_closed_months,
                ).order_by(Spot.spot_id)
            )
        ).all()
        vocabulary_rows = (
            await self.session.execute(
                select(TagVocabulary.tag, TagVocabulary.preference_key).order_by(
                    TagVocabulary.tag
                )
            )
        ).all()
        realtime_rows = (
            await self.session.execute(
                select(
                    SpotRealtime.spot_id,
                    SpotRealtime.weather,
                    SpotRealtime.congestion,
                ).order_by(SpotRealtime.spot_id)
            )
        ).all()

        travel_minutes: dict[str, int] = {}
        if origin_spot_id is not None:
            travel_rows = (
                await self.session.execute(
                    select(TravelTime.to_spot_id, TravelTime.duration_sec)
                    .where(
                        TravelTime.from_spot_id == origin_spot_id,
                        TravelTime.mode == "car",
                    )
                    .order_by(TravelTime.to_spot_id)
                )
            ).all()
            travel_minutes = {
                row.to_spot_id: (int(row.duration_sec) + 59) // 60 for row in travel_rows
            }
            # 行列は同一点行を持たない。
            # 地理計算ではなく既知の恒等値を補う。
            travel_minutes[origin_spot_id] = 0

        spots = tuple(
            RecommendationSpot(
                spot_id=row.spot_id,
                name_ja=row.name_ja,
                description_ja=_japanese_value(row.description),
                address_ja=_japanese_value(row.address),
                tags_ja=tuple(row.tags_ja),
                stay_min=int(row.stay_min),
                weather_fit=row.weather_fit,
                visit_difficulty=row.visit_difficulty,
                season_closed_months=tuple(int(value) for value in row.season_closed_months),
            )
            for row in spot_rows
        )
        names = {spot.spot_id: spot.name_ja for spot in spots}
        return RecommendationData(
            spots=spots,
            tag_to_preference={
                row.tag: (
                    PreferenceKey(row.preference_key)
                    if row.preference_key is not None
                    else None
                )
                for row in vocabulary_rows
            },
            realtime={
                row.spot_id: RealtimeValue(
                    weather=int(row.weather) if row.weather is not None else None,
                    congestion=(
                        int(row.congestion) if row.congestion is not None else None
                    ),
                )
                for row in realtime_rows
            },
            travel_minutes=travel_minutes,
            origin_name_ja=names.get(origin_spot_id) if origin_spot_id is not None else None,
        )

    async def existing_spot_ids(self, spot_ids: list[str]) -> set[str]:
        """LLM 後のクローズドワールド照合。

        候補生成時の読取結果を信用し直さない。
        """

        unique_ids = list(dict.fromkeys(spot_ids))
        if not unique_ids:
            return set()
        rows = await self.session.scalars(
            select(Spot.spot_id).where(Spot.spot_id.in_(unique_ids))
        )
        return set(rows.all())


def _japanese_value(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    japanese = value.get("ja")
    return japanese if isinstance(japanese, str) else None
