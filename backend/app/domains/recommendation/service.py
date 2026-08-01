"""決定的候補生成、暫定通知、任意リランク、DB 照合を束ねる。"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.core.config import Settings, get_settings
from app.core.llm import GenerationClient
from app.domains.recommendation.repo_types import RecommendationData
from app.domains.recommendation.rerank import (
    CandidateReranker,
    LLMRecommendationReranker,
)
from app.domains.recommendation.scoring import (
    DEFAULT_WEIGHTS,
    ScoredSpot,
    ScoringWeights,
    score_catalog,
    to_itinerary_utilities,
)
from app.domains.recommendation.types import (
    CandidateStage,
    CandidateStateEvent,
    RecommendFilter,
    RecommendationCandidate,
    RecommendationContext,
    RecommendationResult,
    RecommendRequest,
)

logger = logging.getLogger(__name__)

CANDIDATE_POOL_SIZE = 10
_JAPAN_TZ = ZoneInfo("Asia/Tokyo")


def _today_in_japan() -> date:
    return datetime.now(_JAPAN_TZ).date()


class RecommendationRepositoryPort(Protocol):
    async def load_data(self, origin_spot_id: str | None = None) -> RecommendationData: ...

    async def existing_spot_ids(self, spot_ids: list[str]) -> set[str]: ...


CandidateEventSink = Callable[[CandidateStateEvent], Awaitable[None] | None]


class RecommendationService:
    def __init__(
        self,
        repository: RecommendationRepositoryPort,
        *,
        settings: Settings | None = None,
        reranker: CandidateReranker | None = None,
        event_sink: CandidateEventSink | None = None,
        candidate_pool_size: int = CANDIDATE_POOL_SIZE,
        weights: ScoringWeights = DEFAULT_WEIGHTS,
        today_provider: Callable[[], date] = _today_in_japan,
    ) -> None:
        if not 8 <= candidate_pool_size <= 12:
            raise ValueError("candidate_pool_size は 8〜12 にしてください")
        self.repository = repository
        self.settings = settings or get_settings()
        self.reranker = reranker
        if self.reranker is None and self.settings.recommendation_rerank_enabled:
            self.reranker = LLMRecommendationReranker(GenerationClient(self.settings))
        self.event_sink = event_sink
        self.candidate_pool_size = candidate_pool_size
        self.weights = weights
        self.today_provider = today_provider

    async def recommend(
        self,
        request: RecommendRequest | Mapping[str, Any] | None = None,
        *,
        context: RecommendationContext | Mapping[str, Any] | None = None,
    ) -> RecommendationResult:
        parsed_request = (
            request
            if isinstance(request, RecommendRequest)
            else RecommendRequest.model_validate(request or {})
        )
        parsed_context = (
            context
            if isinstance(context, RecommendationContext)
            else RecommendationContext.model_validate(context or {})
        )
        _, scored = await self._load_scored(parsed_request, parsed_context)
        provisional = scored[: self.candidate_pool_size]
        provisional_ids = [value.spot.spot_id for value in provisional]
        await self._emit("provisional", provisional)

        final_count = min(parsed_request.k, len(provisional))
        selected_ids = provisional_ids[:final_count]
        rerank_used = False
        if (
            final_count > 0
            and self.settings.recommendation_rerank_enabled
            and self.reranker is not None
        ):
            try:
                returned = list(
                    await self.reranker.rerank(
                        provisional,
                        request=parsed_request,
                        context=parsed_context,
                        k=final_count,
                    )
                )
                db_ids = await self.repository.existing_spot_ids(returned)
                candidate_ids = set(provisional_ids)
                valid = [
                    spot_id
                    for spot_id in dict.fromkeys(returned)
                    if spot_id in candidate_ids and spot_id in db_ids
                ]
                if valid:
                    selected_ids = [
                        *valid[:final_count],
                        *(
                            spot_id
                            for spot_id in provisional_ids
                            if spot_id not in valid
                        ),
                    ][:final_count]
                    rerank_used = True
            except Exception as exc:  # noqa: BLE001 - LLM 障害はすべて縮退対象
                logger.warning(
                    "recommendation_rerank_degraded",
                    extra={"degraded": True, "reason": type(exc).__name__},
                    exc_info=True,
                )

        # 候補ロード後の削除や、LLM の候補外・架空 ID を
        # 最後の DB 読取で閉じる。
        existing_final = await self.repository.existing_spot_ids(selected_ids)
        selected_ids = [spot_id for spot_id in selected_ids if spot_id in existing_final]
        if len(selected_ids) < final_count:
            fallback_existing = await self.repository.existing_spot_ids(provisional_ids)
            selected_ids.extend(
                spot_id
                for spot_id in provisional_ids
                if spot_id in fallback_existing and spot_id not in selected_ids
            )
            selected_ids = selected_ids[:final_count]

        by_id = {value.spot.spot_id: value for value in provisional}
        final_scored = [by_id[spot_id] for spot_id in selected_ids]
        await self._emit("final", final_scored)
        return RecommendationResult(
            spot_ids=selected_ids,
            candidates=_candidate_models(final_scored),
            provisional_spot_ids=provisional_ids,
            rerank_used=rerank_used,
        )

    async def build_itinerary_utilities(
        self,
        request: RecommendRequest | Mapping[str, Any] | None = None,
        *,
        context: RecommendationContext | Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        """LLM を呼ばず、既存 ItineraryService.utilities 用の効用表を返す。"""

        parsed_request = (
            request
            if isinstance(request, RecommendRequest)
            else RecommendRequest.model_validate(request or {})
        )
        parsed_context = (
            context
            if isinstance(context, RecommendationContext)
            else RecommendationContext.model_validate(context or {})
        )
        data, scored = await self._load_scored(parsed_request, parsed_context)
        return to_itinerary_utilities(data, scored)

    async def _load_scored(
        self,
        request: RecommendRequest,
        context: RecommendationContext,
    ) -> tuple[RecommendationData, list[ScoredSpot]]:
        origin_spot_id = context.origin_spot_id(request.filter.day)
        data = await self.repository.load_data(origin_spot_id)
        target_date = context.target_date(request.filter.day, today=self.today_provider())
        scored = score_catalog(
            data,
            request,
            context,
            target_month=target_date.month,
            weights=self.weights,
        )
        return data, scored

    async def _emit(
        self,
        stage: CandidateStage,
        values: Sequence[ScoredSpot],
    ) -> None:
        if self.event_sink is None:
            return
        event = CandidateStateEvent(
            stage=stage,
            spot_ids=[value.spot.spot_id for value in values],
            candidates=_candidate_models(values),
        )
        emitted = self.event_sink(event)
        if inspect.isawaitable(emitted):
            await emitted


def _candidate_models(values: Sequence[ScoredSpot]) -> list[RecommendationCandidate]:
    return [
        RecommendationCandidate(
            spot_id=value.spot.spot_id,
            rank=index,
            reason_materials=value.reason_materials,
        )
        for index, value in enumerate(values, start=1)
    ]


async def recommend(
    repository: RecommendationRepositoryPort,
    *,
    filter: RecommendFilter | Mapping[str, Any] | None = None,
    k: int = 5,
    exclude: Sequence[str] = (),
    context: RecommendationContext | Mapping[str, Any] | None = None,
    settings: Settings | None = None,
    reranker: CandidateReranker | None = None,
    event_sink: CandidateEventSink | None = None,
) -> RecommendationResult:
    """§18.3 の in 契約をそのまま受ける薄い Tool 入口。"""

    parsed_filter = (
        filter
        if isinstance(filter, RecommendFilter)
        else RecommendFilter.model_validate(filter or {})
    )
    request = RecommendRequest(filter=parsed_filter, k=k, exclude=list(exclude))
    return await RecommendationService(
        repository,
        settings=settings,
        reranker=reranker,
        event_sink=event_sink,
    ).recommend(request, context=context)
