"""実カタログに対する推薦結果の実在性と語彙ロードを検証する。"""

import os
from datetime import date

import pytest

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.domains.recommendation.repo import RecommendationRepository
from app.domains.recommendation.service import RecommendationService
from app.domains.recommendation.types import (
    PreferenceKey,
    RecommendationContext,
    RecommendationProfile,
    RecommendFilter,
    RecommendRequest,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


async def test_real_catalog_recommendations_are_closed_world() -> None:
    settings = get_settings().model_copy(update={"recommendation_rerank_enabled": False})
    try:
        async with session_scope(settings) as session:
            repository = RecommendationRepository(session)
            service = RecommendationService(
                repository,
                settings=settings,
                today_provider=lambda: date(2026, 8, 2),
            )
            result = await service.recommend(
                RecommendRequest(
                    filter=RecommendFilter(mobility="avoid_walk", weather_fit=True),
                    k=8,
                ),
                context=RecommendationContext(
                    profile=RecommendationProfile(
                        interests={
                            PreferenceKey.WATER: 0.9,
                            PreferenceKey.NATURE: 0.7,
                        }
                    ),
                    travel_date=date(2026, 8, 2),
                ),
            )
            assert len(result.spot_ids) == 8
            assert await repository.existing_spot_ids(result.spot_ids) == set(
                result.spot_ids
            )

            data = await repository.load_data()
            assert len(data.spots) == 43
            assert len(data.tag_to_preference) == 80
            assert set(value for value in data.tag_to_preference.values() if value) == set(
                PreferenceKey
            )
    finally:
        await dispose_engine()
