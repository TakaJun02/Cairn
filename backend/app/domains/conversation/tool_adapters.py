"""既存 recommendation / itinerary / narration を 5 Tool 契約へ合わせる薄い層。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.core.llm import GenerationClient
from app.domains.conversation.ask_registry import (
    DEFAULT_ASK_TIMEOUT_SEC,
    AskUserRegistry,
    wait_for_answer,
    write_pending_ask_now,
)
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
from app.domains.conversation.tool_ports import SearchAskCallback
from app.domains.conversation.types import (
    AskUserArgs,
    AskUserResult,
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
from app.domains.geo.osrm import OSRMClient, read_osrm_build
from app.domains.geo.repo import GeoRepository
from app.domains.geo.routes import PendingRoute, RouteService, fetch_route_segments
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.itinerary.service import (
    ItineraryService,
    ProvisionalItinerarySink,
    SolutionSelector,
)
from app.domains.itinerary.types import (
    Diff as ItineraryDiff,
)
from app.domains.itinerary.types import (
    Itinerary,
)
from app.domains.itinerary.types import (
    ToolError as ItineraryToolError,
)
from app.domains.narration.search import search_knowledge
from app.domains.narration.search.types import (
    SearchResult,
    SearchStateEvent,
)
from app.domains.narration.search.types import (
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
PendingAskWriter = Callable[..., Awaitable[None]]
# ADR-0020: route の永続化専用の一時 session を開くファクトリ。既定は
# `session_scope`(専用の短寿命 session・即時 commit)。テストで差し替える。
RouteSessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


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
        thread_id: int | None = None,
        user_id: int | None = None,
        ask_registry: AskUserRegistry | None = None,
        ask_timeout_sec: float = DEFAULT_ASK_TIMEOUT_SEC,
        pending_ask_writer: PendingAskWriter | None = None,
        route_session_scope: RouteSessionScopeFactory | None = None,
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
        # `ask_user` の HITL 待ち受け(§7)。1 ユーザー 1 スレッドなので
        # レジストリは user_id で引く(`POST /chat/answer` 側も同じ key)。
        self.thread_id = thread_id
        self.user_id = user_id
        self.ask_registry = ask_registry or AskUserRegistry()
        self.ask_timeout_sec = ask_timeout_sec
        # ADR-0020: route の永続化はターンの session(self.session)を使わず、
        # レッグごとに専用の一時 session で即時 commit する。既定は
        # `session_scope`(実 DB)。テストは差し替えてよい。
        self.route_session_scope: RouteSessionScopeFactory = (
            route_session_scope or (lambda: session_scope(self.settings))
        )
        self.pending_ask_writer = pending_ask_writer or write_pending_ask_now

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
                    stage="recommend",
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
                    assumptions=args.assumptions,
                ),
                selector=selector,
                provisional_sink=provisional_sink,
                stage="plan_itinerary",
            )
        except Exception as exc:  # noqa: BLE001
            return _exception_error(exc)
        if isinstance(result, ItineraryToolError):
            return _convert_itinerary_error(result)
        payload = result.model_dump(mode="json", by_alias=True)
        selection_degraded = await self._selection_degradation(selector, stage="plan_itinerary")
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
        # ADR-0021: 集合固定(allow_refill=False)では解の多様化と LLM 選択を
        # 省略するため、選択に使う LLM selector 自体を作らない
        # (`use_specialist` を False にする)。
        selector = self._itinerary_selector(
            use_specialist=use_specialist and args.allow_refill and not is_revert,
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
                    allow_refill=args.allow_refill,
                    assumptions=args.assumptions,
                ),
                selector=selector,
                provisional_sink=provisional_sink,
                stage="edit_itinerary",
            )
        except Exception as exc:  # noqa: BLE001
            return _exception_error(exc)
        if isinstance(result, ItineraryToolError):
            return _convert_itinerary_error(result)
        payload = result.model_dump(mode="json", by_alias=True)
        selection_degraded = await self._selection_degradation(selector, stage="edit_itinerary")
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
        ask_callback: SearchAskCallback | None = None,
    ) -> ToolResult | ToolError:
        async def searching_sink(value: SearchStateEvent) -> None:
            # 旧 searching は state:step に統合する(§11)。narration ドメイン
            # 内部(callback の型)は変えず、ここで変換だけ行う。
            await emit(
                self.event_sink,
                state_event(
                    "step",
                    tool="search_knowledge",
                    status="progress",
                    label_ja=value.text,
                ),
            )

        try:
            result = await self.search_runner(
                args.request,
                args.spot_id,
                settings=self.settings,
                event_sink=searching_sink,
                ask_callback=ask_callback,
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
        """`ask_user` の HITL 実体(§7)。

        2026-08-04 レビュー是正(High・裁定6): 手順を
        「① レジストリに waiter 登録 → ② `state:ask_user`/`clarify` を送出
        (ストリームは開いたまま) → ③ `threads.pending_ask` を別トランザク
        ションで即時反映 → ④ 回答を待つ(タイムアウト 10 分) → ⑤ 回答受領・
        タイムアウトいずれでも `pending_ask` を即時 NULL に戻す → ⑥
        `AskResult{answer, answered_by}` を呼び出し元へそのまま返す」に変更
        した。旧順序(SSE 送出 → DB 書き込み → レジストリ登録)には 2 つの
        競合窓があった: (a) 表示直後にユーザーが即答すると waiter が未登録で
        409 になる、(b) ② と ③ の間に来た `GET /thread` が「pending_ask は
        あるが waiter は無い」を「死んだ待機」と誤認して掃除してしまう。
        ① を最初にすることで `GET /thread`(`ask_registry.is_waiting`)は
        waiter 登録済みを live と判定でき、両方の競合窓が閉じる。
        """

        future = None
        if self.user_id is not None:
            future = self.ask_registry.begin(self.user_id)

        if args.kind == "preference":
            await emit(
                self.event_sink,
                state_event(
                    "ask_user",
                    slot=args.slot.value if args.slot is not None else None,
                    reason=args.reason,
                    options=[option.label for option in args.options],
                ),
            )
        else:
            await emit(
                self.event_sink,
                state_event(
                    "clarify",
                    surface=args.surface,
                    reason=args.reason,
                    options=[
                        {"label": option.label, "value": option.value}
                        for option in args.options
                    ],
                ),
            )

        pending = args.model_dump(mode="json", exclude_none=True)
        pending["asked_at"] = _utcnow_iso()
        if self.thread_id is not None:
            await self.pending_ask_writer(
                thread_id=self.thread_id, pending=pending, settings=self.settings
            )

        answer = None
        if self.user_id is not None:
            answer = await wait_for_answer(
                self.ask_registry,
                key=self.user_id,
                future=future,
                timeout_sec=self.ask_timeout_sec,
            )

        if self.thread_id is not None:
            await self.pending_ask_writer(
                thread_id=self.thread_id, pending=None, settings=self.settings
            )

        if answer is None:
            result = AskUserResult(
                answer="(タイムアウトのため回答がありませんでした)",
                answered_by="timeout",
                slot=args.slot,
                surface=args.surface,
            )
        else:
            result = AskUserResult(
                answer=answer.answer,
                answered_by=answer.answered_by,
                slot=args.slot,
                surface=args.surface,
            )
        return ToolResult(
            step_id=step_id,
            tool=ToolName.ASK_USER,
            data=result.model_dump(mode="json", exclude_none=True),
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
        stage: Literal["plan_itinerary", "edit_itinerary"],
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
                # BUILD ファイルが読めるかだけを事前確認する(以前は使い捨ての
                # RouteService を構築して確かめていたが、ADR-0020 でレッグごと
                # に専用 session を開くようになったため、ここでは軽い事前検査
                # だけ行い、実際の RouteService はレッグ単位で作る)。
                osrm_build = read_osrm_build()
            except Exception:  # noqa: BLE001 - BUILD 不在なら route なしで局所縮退
                initialization_failed = True
            else:
                provider = _FallbackRouteProvider(
                    settings=self.settings,
                    osrm=osrm,
                    osrm_build=osrm_build,
                    session_scope=self.route_session_scope,
                )
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
                            stage=stage,
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
                    stage=stage,
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
        *,
        stage: Literal["plan_itinerary", "edit_itinerary"],
    ) -> list[str]:
        if selector is None or not selector.degraded:
            return []
        await emit(
            self.event_sink,
            error_event(
                stage=stage,
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
        # `assumptions` は `Itinerary` に既に載っている(itinerary/types.py)。
        # chat_sse.md §1.2 の契約どおり、state:itinerary のトップレベルにも
        # そのまま複製する(2026-08-04 追加。provisional/final とも。
        # Docs/30_design/agent_react_architecture.md §5)。
        await emit(
            self.event_sink,
            state_event(
                "itinerary",
                phase=phase,
                version=itinerary["version"],
                itinerary=itinerary,
                diff=diff,
                concessions=concessions,
                assumptions=itinerary.get("assumptions", []),
            ),
        )


# 2026-08-04 レビュー是正(High・H-1): レッグ取得の並列度を絞る定数。
# 既定プールは size 5 + overflow 10 = 同時 15 接続で、進行中ターン 1 本が
# 主 session を常時占有するため、無制限並列(12 レッグ一斉)では同時 2 ターン
# で飽和する(NFR-8「同時〜数十」に足りない)。設定化はしない
# (ADR-0020「注意」)。
ROUTE_LEG_CONCURRENCY = 4


class _FallbackRouteProvider:
    """レッグごとに route を取得・永続化する(ADR-0020)。

    `_attach_route_ids`(itinerary/service.py)は全レッグを `asyncio.gather`
    で並列に呼ぶため、AsyncSession を使い回さず、レッグごとに専用の
    短寿命 session を開いてその場で commit する。これにより
    `state:itinerary`(final)送出の時点で、そこに載る全 `route_id` が
    commit 済みであることが保証される(SSE 契約。chat_sse.md §1.2)。

    2026-08-04 レビュー是正(High・H-1): DB session は「キャッシュ照会」と
    「保存」だけを包み、OSRM への HTTP 往復(最大 `osrm_leg_timeout_sec`
    秒)は session の外で行う(`RouteService.lookup_or_prepare` →
    `fetch_route_segments`(session なし)→ `RouteService.save` の 3 段)。
    さらに `asyncio.Semaphore` でレッグの並列度を絞り、DB 接続と OSRM
    リクエストの双方が無制限に膨らまないようにする。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        osrm: OSRMClient,
        osrm_build: str,
        session_scope: RouteSessionScopeFactory,
        concurrency: int = ROUTE_LEG_CONCURRENCY,
        repository_factory: Callable[[AsyncSession], Any] = GeoRepository,
    ) -> None:
        self.settings = settings
        self.osrm = osrm
        self.osrm_build = osrm_build
        self.session_scope = session_scope
        self.degraded = False
        self._semaphore = asyncio.Semaphore(concurrency)
        # テスト用の差し替え口(M-1): 既定は実 DB を叩く `GeoRepository`。
        # `RouteRepository` プロトコルを満たす fake を注入すれば、DB を
        # 使わずに commit タイミングだけを検証できる。
        self._repository_factory = repository_factory

    async def route_id_for_leg(
        self,
        source: str,
        target: str,
        mode: Any,
    ) -> str | None:
        del mode  # RouteService が car/foot の接近を一つの route にまとめる。
        try:
            async with self._semaphore:
                # 段1: 照会(DB のみ)。`session_scope` の契約: with を正常に
                # 抜けたら commit する(`app.core.db.session_scope` と同じ
                # 契約。テストで差し替えるときもこの契約を満たす)。
                async with self.session_scope() as lookup_session:
                    lookup_service = RouteService(
                        self._repository_factory(lookup_session),
                        self.osrm,
                        self.settings,
                        osrm_build=self.osrm_build,
                    )
                    pending = await lookup_service.lookup_or_prepare(
                        {"spot_id": source},
                        {"spot_id": target},
                    )
                if not isinstance(pending, PendingRoute):
                    # 冪等キャッシュにヒット済み。OSRM も追加の session も不要。
                    return str(pending.route_id)

                # 段2: OSRM への HTTP 往復。DB session は一切握らない。
                assembled = await fetch_route_segments(
                    self.osrm, pending, settings=self.settings
                )

                # 段3: 保存(DB のみ)。commit 完了後にのみ route_id を返す。
                async with self.session_scope() as save_session:
                    save_service = RouteService(
                        self._repository_factory(save_session),
                        self.osrm,
                        self.settings,
                        osrm_build=self.osrm_build,
                    )
                    saved = await save_service.save(pending, assembled)
                return str(saved.route_id)
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


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
