"""既存 recommendation / itinerary / narration を 5 Tool 契約へ合わせる薄い層。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.llm import GenerationClient
from app.domains.conversation.events import (
    EventSinkLike,
    emit,
    error_event,
    state_event,
)
from app.domains.conversation.itinerary_selector import (
    LLMItinerarySelector,
    SelectorGenerationPort,
)
from app.domains.conversation.types import (
    AskUserArgs,
    ConstraintDraft,
    EditItineraryArgs,
    PlanItineraryArgs,
    RecommendArgs,
    SearchKnowledgeArgs,
    ToolError,
    ToolErrorCode,
    ToolName,
    ToolResult,
    constraint_to_mapping,
)
from app.domains.geo.osrm import OSRMClient
from app.domains.geo.repo import GeoRepository
from app.domains.geo.routes import RouteService
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.itinerary.service import (
    ItineraryService,
    ProvisionalItinerarySink,
    SolutionSelector,
)
from app.domains.itinerary.types import (
    Diff as ItineraryDiff,
    Itinerary,
    ToolError as ItineraryToolError,
)
from app.domains.narration.search import search_knowledge
from app.domains.narration.search.types import (
    SearchResult,
    SearchStateEvent,
    ToolError as SearchToolError,
)
from app.domains.recommendation.repo import RecommendationRepository
from app.domains.recommendation.rerank import LLMRecommendationReranker
from app.domains.recommendation.service import RecommendationService
from app.domains.recommendation.types import (
    CandidateStateEvent,
    RecommendationContext,
)

SearchRunner = Callable[..., Awaitable[SearchResult | SearchToolError]]


class ToolAdapters:
    """TurnState を知らず、明示引数だけで既存ドメインへ委譲する。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        event_sink: EventSinkLike = None,
        settings: Settings | None = None,
        spot_names: Mapping[str, str] | None = None,
        search_runner: SearchRunner = search_knowledge,
        attach_routes: bool = True,
        generation_client: SelectorGenerationPort | None = None,
    ) -> None:
        self.session = session
        self.event_sink = event_sink
        self.settings = settings or get_settings()
        self.spot_names = dict(spot_names or {})
        self.search_runner = search_runner
        self.attach_routes = attach_routes
        self.generation_client = generation_client or GenerationClient(self.settings)
        self.recommendation_repository = RecommendationRepository(session)
        self.itinerary_repository = ItineraryRepository(session)

    async def recommend(
        self,
        *,
        step_id: int,
        args: RecommendArgs,
        context: RecommendationContext,
        use_specialist: bool,
    ) -> ToolResult | ToolError:
        async def candidate_sink(value: CandidateStateEvent) -> None:
            await emit(
                self.event_sink,
                state_event(
                    "candidates",
                    phase=value.stage,
                    items=[
                        {
                            "spot_id": candidate.spot_id,
                            "name_ja": self.spot_names.get(
                                candidate.spot_id, candidate.spot_id
                            ),
                            "reason_materials": candidate.reason_materials.model_dump(
                                mode="json"
                            ),
                        }
                        for candidate in value.candidates
                    ],
                ),
            )

        settings = self.settings.model_copy(
            update={
                "recommendation_rerank_enabled": bool(
                    use_specialist and self.settings.recommendation_rerank_enabled
                )
            }
        )
        try:
            reranker = (
                LLMRecommendationReranker(self.generation_client)  # type: ignore[arg-type]
                if settings.recommendation_rerank_enabled
                else None
            )
            service = RecommendationService(
                self.recommendation_repository,
                settings=settings,
                reranker=reranker,
                event_sink=candidate_sink,
            )
            result = await service.recommend(
                {
                    "filter": args.filter,
                    "k": args.k,
                    "exclude": args.exclude,
                },
                context=context,
            )
        except Exception as exc:  # noqa: BLE001 - adapter が例外を ToolError に閉じる
            return _exception_error(exc)
        if not result.spot_ids:
            return ToolError(
                code=ToolErrorCode.EMPTY_RESULT,
                message_ja="条件に合う候補が見つかりませんでした。",
                recoverable=True,
                details={"step_id": step_id},
            )
        degraded: list[str] = []
        if use_specialist and settings.recommendation_rerank_enabled and not result.rerank_used:
            degraded.append("rerank_degraded")
            await emit(
                self.event_sink,
                error_event(
                    stage="act",
                    code="rerank_degraded",
                    degraded=True,
                    message="候補をスコア順で確定しました",
                ),
            )
        return ToolResult(
            step_id=step_id,
            tool=ToolName.RECOMMEND,
            data=result.model_dump(mode="json"),
            degraded=degraded,
        )

    async def plan_itinerary(
        self,
        *,
        step_id: int,
        user_id: int,
        args: PlanItineraryArgs,
        constraints: Sequence[ConstraintDraft],
        selection_text: str,
        recommendation_context: RecommendationContext,
        use_specialist: bool,
    ) -> ToolResult | ToolError:
        provisional_sink = self._provisional_itinerary_sink()
        selector = self._itinerary_selector(
            use_specialist=use_specialist,
            selection_text=selection_text,
        )
        try:
            utilities = await self._itinerary_utilities(recommendation_context)
            result, route_degraded = await self._run_itinerary(
                lambda service: service.plan_itinerary(
                    user_id=user_id,
                    days=args.days,
                    must_visit=args.must_visit,
                    constraints=[constraint_to_mapping(value) for value in constraints],
                    utilities=utilities,
                    selection_text=selection_text,
                ),
                selector=selector,
                provisional_sink=provisional_sink,
            )
        except Exception as exc:  # noqa: BLE001
            return _exception_error(exc)
        if isinstance(result, ItineraryToolError):
            return _convert_itinerary_error(result)
        payload = result.model_dump(mode="json", by_alias=True)
        selection_degraded = await self._selection_degradation(selector)
        if selection_degraded:
            payload["selection_used"] = False
        await self._emit_itinerary_state(
            payload["itinerary"],
            phase="final",
            diff=_empty_diff(),
            concessions=payload.get("concessions", []),
        )
        degraded = [
            *(["route_degraded"] if route_degraded else []),
            *selection_degraded,
        ]
        return ToolResult(
            step_id=step_id,
            tool=ToolName.PLAN_ITINERARY,
            data=payload,
            degraded=degraded,
        )

    async def edit_itinerary(
        self,
        *,
        step_id: int,
        user_id: int,
        args: EditItineraryArgs,
        constraints: Sequence[ConstraintDraft],
        constraints_remove: Sequence[str],
        selection_text: str,
        recommendation_context: RecommendationContext,
        use_specialist: bool,
    ) -> ToolResult | ToolError:
        is_revert = any(value.get("op") == "revert" for value in args.ops)
        provisional_sink = self._provisional_itinerary_sink()
        selector = self._itinerary_selector(
            use_specialist=use_specialist and not is_revert,
            selection_text=selection_text,
        )
        try:
            utilities = await self._itinerary_utilities(recommendation_context)
            result, route_degraded = await self._run_itinerary(
                lambda service: service.edit_itinerary(
                    user_id=user_id,
                    ops=args.ops,
                    constraints=[constraint_to_mapping(value) for value in constraints],
                    constraints_remove=constraints_remove,
                    utilities=utilities,
                    selection_text=selection_text,
                ),
                selector=selector,
                provisional_sink=provisional_sink,
            )
        except Exception as exc:  # noqa: BLE001
            return _exception_error(exc)
        if isinstance(result, ItineraryToolError):
            return _convert_itinerary_error(result)
        payload = result.model_dump(mode="json", by_alias=True)
        selection_degraded = await self._selection_degradation(selector)
        if selection_degraded:
            payload["selection_used"] = False
        await self._emit_itinerary_state(
            payload["itinerary"],
            phase="final",
            diff=payload.get("diff", _empty_diff()),
            concessions=payload.get("concessions", []),
        )
        degraded = [
            *(["route_degraded"] if route_degraded else []),
            *selection_degraded,
        ]
        return ToolResult(
            step_id=step_id,
            tool=ToolName.EDIT_ITINERARY,
            data=payload,
            degraded=degraded,
        )

    async def search_knowledge(
        self,
        *,
        step_id: int,
        args: SearchKnowledgeArgs,
    ) -> ToolResult | ToolError:
        async def searching_sink(value: SearchStateEvent) -> None:
            await emit(self.event_sink, state_event("searching", text=value.text))

        try:
            result = await self.search_runner(
                args.request,
                args.spot_id,
                settings=self.settings,
                event_sink=searching_sink,
            )
        except Exception as exc:  # noqa: BLE001
            return _exception_error(exc)
        if isinstance(result, SearchToolError):
            return ToolError(
                code=ToolErrorCode(result.code.value),
                message_ja=result.message_ja,
                recoverable=False,
                details=result.details,
            )
        payload = result.model_dump(mode="json")
        payload["spot_id"] = args.spot_id
        return ToolResult(
            step_id=step_id,
            tool=ToolName.SEARCH_KNOWLEDGE,
            data=payload,
        )

    async def ask_user(
        self,
        *,
        step_id: int,
        args: AskUserArgs,
    ) -> ToolResult | ToolError:
        payload = {
            "slot": args.slot.value,
            "reason": args.reason,
            "options": args.options,
        }
        await emit(
            self.event_sink,
            state_event("ask_user", slot=args.slot.value, options=args.options),
        )
        return ToolResult(
            step_id=step_id,
            tool=ToolName.ASK_USER,
            data=payload,
        )

    async def _itinerary_utilities(
        self,
        context: RecommendationContext,
    ) -> dict[str, float]:
        settings = self.settings.model_copy(
            update={"recommendation_rerank_enabled": False}
        )
        service = RecommendationService(
            self.recommendation_repository,
            settings=settings,
        )
        return await service.build_itinerary_utilities(context=context)

    async def _run_itinerary(
        self,
        call: Callable[[ItineraryService], Awaitable[Any]],
        *,
        selector: SolutionSelector | None,
        provisional_sink: ProvisionalItinerarySink | None,
    ) -> tuple[Any, bool]:
        if not self.attach_routes:
            return (
                await call(
                    ItineraryService(
                        self.itinerary_repository,
                        selector=selector,
                        provisional_sink=provisional_sink,
                    )
                ),
                False,
            )
        initialization_failed = False
        async with OSRMClient(self.settings) as osrm:
            try:
                route_service = RouteService(
                    GeoRepository(self.session),
                    osrm,
                    self.settings,
                )
            except Exception:  # noqa: BLE001 - BUILD 不在なら route なしで局所縮退
                initialization_failed = True
            else:
                provider = _FallbackRouteProvider(route_service)
                # Tool 本体は必ず 1 回だけ実行する。
                # ここでの例外は呼び出し元が
                # ToolError へ変換し、二重実行しない。
                result = await call(
                    ItineraryService(
                        self.itinerary_repository,
                        selector=selector,
                        route_provider=provider,
                        provisional_sink=provisional_sink,
                    )
                )
                if provider.degraded:
                    await emit(
                        self.event_sink,
                        error_event(
                            stage="act",
                            code="route_degraded",
                            degraded=True,
                            message=(
                                "一部の経路形状を取得できず、"
                                "移動時間のみで確定しました"
                            ),
                        ),
                    )
                return result, provider.degraded
        if initialization_failed:
            await emit(
                self.event_sink,
                error_event(
                    stage="act",
                    code="route_degraded",
                    degraded=True,
                    message=(
                        "経路形状を取得できないため、"
                        "既存の移動時間で続行します"
                    ),
                ),
            )
            result = await call(
                ItineraryService(
                    self.itinerary_repository,
                    selector=selector,
                    provisional_sink=provisional_sink,
                )
            )
            return result, True
        raise RuntimeError("RouteService の初期化状態が不正です")  # pragma: no cover

    def _itinerary_selector(
        self,
        *,
        use_specialist: bool,
        selection_text: str,
    ) -> LLMItinerarySelector | None:
        if not use_specialist or not selection_text.strip():
            return None
        return LLMItinerarySelector(self.generation_client)

    async def _selection_degradation(
        self,
        selector: LLMItinerarySelector | None,
    ) -> list[str]:
        if selector is None or not selector.degraded:
            return []
        await emit(
            self.event_sink,
            error_event(
                stage="act",
                code="selection_degraded",
                degraded=True,
                message="旅程候補は決定的な解 A で確定しました",
            ),
        )
        return ["selection_degraded"]

    def _provisional_itinerary_sink(self) -> ProvisionalItinerarySink:
        async def provisional(itinerary: Itinerary, diff: ItineraryDiff) -> None:
            await self._emit_itinerary_state(
                itinerary.model_dump(mode="json", by_alias=True),
                phase="provisional",
                diff=diff.model_dump(mode="json", by_alias=True),
                concessions=[
                    value.model_dump(mode="json")
                    for value in itinerary.concessions
                ],
            )

        return provisional

    async def _emit_itinerary_state(
        self,
        itinerary: dict[str, Any],
        *,
        phase: str,
        diff: dict[str, Any],
        concessions: list[Any],
    ) -> None:
        await emit(
            self.event_sink,
            state_event(
                "itinerary",
                phase=phase,
                version=itinerary["version"],
                itinerary=itinerary,
                diff=diff,
                concessions=concessions,
            ),
        )


class _FallbackRouteProvider:
    def __init__(self, service: RouteService) -> None:
        self.service = service
        self.degraded = False

    async def route_id_for_leg(
        self,
        source: str,
        target: str,
        mode: Any,
    ) -> str | None:
        del mode  # RouteService が car/foot の接近を一つの route にまとめる。
        try:
            route = await self.service.get_or_create(
                {"spot_id": source},
                {"spot_id": target},
            )
            return str(route.route_id)
        # 移動時間行列は既にあるため局所縮退できる。
        except Exception:  # noqa: BLE001
            self.degraded = True
            return None


def _convert_itinerary_error(value: ItineraryToolError) -> ToolError:
    return ToolError(
        code=ToolErrorCode(value.code.value),
        message_ja=value.message_ja,
        recoverable=value.recoverable,
        details=value.details,
    )


def _exception_error(exc: Exception) -> ToolError:
    timeout_types = (TimeoutError, asyncio.TimeoutError)
    is_timeout = isinstance(exc, timeout_types)
    return ToolError(
        code=(
            ToolErrorCode.UPSTREAM_TIMEOUT if is_timeout else ToolErrorCode.INTERNAL
        ),
        message_ja=(
            "外部サービスが時間内に応答しませんでした。"
            if is_timeout
            else "処理中に予期しない問題が発生しました。"
        ),
        recoverable=False,
        details={"error_type": type(exc).__name__},
    )


def _empty_diff() -> dict[str, list[Any]]:
    return {"added": [], "removed": [], "moved": [], "retimed": []}
