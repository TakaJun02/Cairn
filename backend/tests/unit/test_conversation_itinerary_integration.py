"""段4の受け入れ条件(統合テスト・モック LLM)。

`Docs/30_design/agent_react_architecture.md` §5 の指示書が求める受け入れ条件:
(a) 複合要求: 「recommend → plan_itinerary(must_visit に推薦結果の名前)→ done」の
    3周ターンが通り、旅程 v1 と軌跡ダイジェストができる。
(b) 編集と undo: 既存旅程に対し「edit_itinerary(add)→ done」の後、
    「edit_itinerary(revert)→ done」で version が戻る。

`recommendation`/`itinerary` ドメインの内部は一切変更していない。ここでは
実装をそのまま、DB を使わないインメモリ repository で駆動する
(`test_conversation_recommend_integration.py`/`test_itinerary_service.py` と
同じ方針)。実 LLM(127.0.0.1:8000)は叩かない。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from app.core.config import Settings
from app.domains.conversation.main_agent import run_main_agent
from app.domains.conversation.state import ItineraryState, ProfileState, SpotFact, TurnState
from app.domains.conversation.types import (
    ToolError as ConversationToolError,
)
from app.domains.conversation.types import (
    ToolErrorCode,
    ToolName,
    ToolResult,
    constraint_to_mapping,
)
from app.domains.itinerary.repo_types import ItineraryVersion, PlanningData
from app.domains.itinerary.service import ItineraryService, SolverConfig
from app.domains.itinerary.solver import PlanningSpot, TravelTimeMatrix
from app.domains.itinerary.types import Itinerary
from app.domains.itinerary.types import ToolError as ItineraryToolError
from app.domains.recommendation.repo_types import RecommendationData, RecommendationSpot
from app.domains.recommendation.service import RecommendationService

_NO_RERANK_SETTINGS = Settings(recommendation_rerank_enabled=False)
_FAST_SOLVER = SolverConfig(iterations=20, minimum_iterations=20, time_limit_ms=2_000)


def _turn_json(tool: str, args: dict[str, Any], *, thought: str = "考える") -> str:
    return json.dumps(
        {"thought": thought, "action": {"tool": tool, "args": args}}, ensure_ascii=False
    )


def _recommend_act_json(*, filter: dict[str, Any]) -> str:
    return json.dumps(
        {
            "thought": "指示を条件に翻訳する",
            "action": {"tool": "done", "args": {"filter": filter, "assumptions": []}},
        },
        ensure_ascii=False,
    )


class ScriptedMainAgentClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        del messages, kwargs
        return self.responses.pop(0)


class MemoryItineraryRepository:
    """`ItineraryRepository` を満たすインメモリ実装(`test_itinerary_service.py` と同じ方針)。"""

    def __init__(self, planning: PlanningData) -> None:
        self.planning = planning
        self.pending: list[dict[str, Any]] = []
        self.versions: dict[int, ItineraryVersion] = {}
        self.current_version: int | None = None
        self.append_count = 0

    async def get_current(
        self, user_id: int, *, for_update: bool = False
    ) -> ItineraryVersion | None:
        del user_id, for_update
        if self.current_version is None:
            return None
        return self.versions[self.current_version]

    async def load_planning_data(self) -> PlanningData:
        return self.planning

    async def get_pending_constraints(
        self, user_id: int, *, for_update: bool = False
    ) -> list[dict[str, Any]]:
        del user_id, for_update
        return [dict(value) for value in self.pending]

    async def clear_pending_constraints(self, user_id: int) -> None:
        del user_id
        self.pending = []

    async def append_version(
        self,
        *,
        user_id: int,
        itinerary: Itinerary,
        constraints: list[Any],
        origin: str,
        created_by_message_id: int | None = None,
        expected_parent_version: int | None = None,
    ) -> ItineraryVersion:
        parent = self.current_version
        if expected_parent_version is not None:
            assert parent == expected_parent_version
        version = max(self.versions, default=0) + 1
        if parent is not None:
            previous = self.versions[parent]
            self.versions[parent] = replace(previous, is_current=False)
        stored_itinerary = itinerary.model_copy(update={"version": version}, deep=True)
        serialized = [
            value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
            for value in constraints
        ]
        result = ItineraryVersion(
            user_id=user_id,
            version=version,
            parent_version=parent,
            is_current=True,
            itinerary=stored_itinerary,
            constraints=serialized,
            origin=origin,
            created_by_message_id=created_by_message_id,
        )
        self.versions[version] = result
        self.current_version = version
        self.append_count += 1
        return result

    async def revert(self, user_id: int, *, to_version: int | None = None) -> ItineraryVersion:
        del user_id
        assert self.current_version is not None
        current = self.versions[self.current_version]
        target = to_version if to_version is not None else current.parent_version
        assert target is not None
        self.versions[self.current_version] = replace(current, is_current=False)
        target_row = self.versions[target]
        moved = replace(target_row, is_current=True)
        self.versions[target] = moved
        self.current_version = target
        return moved


class MemoryRecommendationRepository:
    def __init__(self, data: RecommendationData) -> None:
        self.data = data

    async def load_data(self, origin_spot_id: str | None = None) -> RecommendationData:
        del origin_spot_id
        return self.data

    async def existing_spot_ids(self, spot_ids: list[str]) -> set[str]:
        return set(spot_ids) & self.data.spot_ids


class RealItineraryTools:
    """段4の接続点(名寄せ→既存 Tool→整形)だけを検査する最小 Tool アダプタ。

    `RecommendationService`/`ItineraryService`(既存の推薦・旅程処理)は一切
    変更せずそのまま使う。OSRM/LLM 選択は使わない(このテストの関心は
    メインループ→旅程計画サブエージェント→既存 Tool の配線であり、
    経路取得・解選択 LLM の検証はそれぞれの専用テストの担当)。
    """

    def __init__(
        self,
        *,
        recommendation_repository: MemoryRecommendationRepository,
        itinerary_repository: MemoryItineraryRepository,
    ) -> None:
        self.recommendation_repository = recommendation_repository
        self.itinerary_repository = itinerary_repository

    async def recommend(
        self, *, step_id: int, args: Any, context: Any, use_specialist: bool
    ) -> ToolResult | ConversationToolError:
        del use_specialist
        service = RecommendationService(
            self.recommendation_repository, settings=_NO_RERANK_SETTINGS
        )
        result = await service.recommend(
            {"filter": args.filter, "k": args.k, "exclude": args.exclude}, context=context
        )
        if not result.spot_ids:
            return ConversationToolError(
                code=ToolErrorCode.EMPTY_RESULT,
                message_ja="候補が見つかりませんでした。",
                recoverable=True,
            )
        return ToolResult(
            step_id=step_id, tool=ToolName.RECOMMEND, data=result.model_dump(mode="json")
        )

    async def plan_itinerary(
        self,
        *,
        step_id: int,
        user_id: int,
        args: Any,
        constraints: Any,
        selection_text: str,
        recommendation_context: Any,
        use_specialist: bool,
    ) -> ToolResult | ConversationToolError:
        del recommendation_context, use_specialist
        service = ItineraryService(self.itinerary_repository, solver_config=_FAST_SOLVER)  # type: ignore[arg-type]
        result = await service.plan_itinerary(
            user_id=user_id,
            days=args.days,
            must_visit=args.must_visit,
            candidate_spots=args.candidate_spots,
            constraints=[constraint_to_mapping(value) for value in constraints],
            selection_text=selection_text,
        )
        if isinstance(result, ItineraryToolError):
            return _convert(result)
        return ToolResult(
            step_id=step_id,
            tool=ToolName.PLAN_ITINERARY,
            data=result.model_dump(mode="json", by_alias=True),
        )

    async def edit_itinerary(
        self,
        *,
        step_id: int,
        user_id: int,
        args: Any,
        constraints: Any,
        constraints_remove: Any,
        selection_text: str,
        recommendation_context: Any,
        use_specialist: bool,
    ) -> ToolResult | ConversationToolError:
        del recommendation_context, use_specialist
        service = ItineraryService(self.itinerary_repository, solver_config=_FAST_SOLVER)  # type: ignore[arg-type]
        result = await service.edit_itinerary(
            user_id=user_id,
            ops=args.ops,
            constraints=[constraint_to_mapping(value) for value in constraints],
            constraints_remove=list(constraints_remove),
            selection_text=selection_text,
        )
        if isinstance(result, ItineraryToolError):
            return _convert(result)
        return ToolResult(
            step_id=step_id,
            tool=ToolName.EDIT_ITINERARY,
            data=result.model_dump(mode="json", by_alias=True),
        )

    async def search_knowledge(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")

    async def ask_user(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("このテストでは呼ばれません")


def _convert(value: ItineraryToolError) -> ConversationToolError:
    return ConversationToolError(
        code=ToolErrorCode(value.code.value),
        message_ja=value.message_ja,
        recoverable=value.recoverable,
        details=value.details,
    )


def _planning() -> PlanningData:
    spots = {
        "spot_origin": PlanningSpot("spot_origin", ("宿泊施設",), 15, kind="facility"),
        "spot_falls": PlanningSpot("spot_falls", ("滝", "自然"), 30),
        "spot_shrine": PlanningSpot("spot_shrine", ("神社",), 30),
    }
    legs = {
        (source, target, "car"): 10 * 60
        for source in spots
        for target in spots
        if source != target
    }
    return PlanningData(spots=spots, travel_times=TravelTimeMatrix(legs))


def _spot_catalog() -> dict[str, SpotFact]:
    return {
        "spot_origin": SpotFact(spot_id="spot_origin", name_ja="道の駅象潟", kind="facility"),
        "spot_falls": SpotFact(
            spot_id="spot_falls", name_ja="銚子ヶ滝", kind="poi", tags_ja=["滝", "自然"]
        ),
        "spot_shrine": SpotFact(
            spot_id="spot_shrine", name_ja="鳥海山神社", kind="poi", tags_ja=["神社"]
        ),
    }


def _recommendation_data() -> RecommendationData:
    spots = (
        RecommendationSpot(
            spot_id="spot_falls",
            name_ja="銚子ヶ滝",
            description_ja=None,
            address_ja="秋田県にかほ市",
            tags_ja=("滝", "自然"),
            stay_min=30,
            weather_fit="rain_ok",
            visit_difficulty="no_walk",
        ),
    )
    return RecommendationData(
        spots=spots,
        tag_to_preference={"滝": None, "自然": None},
        realtime={},
        travel_minutes={},
    )


def _state(**overrides: Any) -> TurnState:
    spots = _spot_catalog()
    base = dict(
        turn_id="turn",
        thread_id=1,
        user_id=7,
        utterance="発話",
        profile=ProfileState(),
        spot_id_vocab=list(spots),
        spot_names={key: value.name_ja for key, value in spots.items()},
        spot_catalog=spots,
        tag_vocabulary=["滝", "自然", "神社"],
        default_origin_spot_id="spot_origin",
    )
    base.update(overrides)
    return TurnState(**base)


async def test_recommend_then_plan_itinerary_then_done_produces_itinerary_v1() -> None:
    """受け入れ条件(a): 複合要求が3周ターンで通り、旅程v1と軌跡ダイジェストができる。"""

    state = _state(utterance="滝を見たい。旅程も作って")
    tools = RealItineraryTools(
        recommendation_repository=MemoryRecommendationRepository(_recommendation_data()),
        itinerary_repository=MemoryItineraryRepository(_planning()),
    )
    client = ScriptedMainAgentClient(
        [
            _turn_json("recommend", {"instruction": "滝を見たい"}),
            _recommend_act_json(filter={"tags": ["滝"]}),
            _turn_json(
                "plan_itinerary",
                {
                    "days": [
                        {
                            "date": "2026-08-10",
                            "start": "09:00",
                            "end": "17:00",
                            "origin_name": None,
                            "destination_name": None,
                        }
                    ],
                    "must_visit": ["銚子ヶ滝"],
                    "constraints": None,
                    "notes": None,
                },
            ),
            _turn_json("done", {}),
        ]
    )

    await run_main_agent(state, tools=tools, client=client)

    assert state.main_agent_failed is False
    # ③ recommend → plan_itinerary → done の3周ターン(main_agent_turns)。
    assert state.main_agent_turns == 3
    assert state.executed_tool_count == 2
    assert [step.tool for step in state.trajectory] == ["recommend", "plan_itinerary"]
    assert all(step.error is None for step in state.trajectory)

    # recommend 結果の名前(スポット名)が must_visit の名寄せに使われている。
    assert "spot_falls" not in state.trajectory[1].observation
    assert "銚子ヶ滝" in state.trajectory[1].observation

    assert state.itinerary is not None
    assert state.itinerary.version == 1
    assert "spot_falls" in [item.spot_id for item in state.itinerary.itinerary.days[0].items]


async def test_edit_add_then_done_then_revert_then_done_restores_version() -> None:
    """受け入れ条件(b): edit(add)→done の後、edit(revert)→done で version が戻る。"""

    itinerary_repository = MemoryItineraryRepository(_planning())
    itinerary_service = ItineraryService(itinerary_repository, solver_config=_FAST_SOLVER)  # type: ignore[arg-type]
    # 窓を60分に絞り、v1(徒歩の元滝相当1件)が神社まで自然に含めない
    # ようにする(そうしないと undo の検証対象がソルバーの自由最適化と
    # 見分けられない)。
    planned = await itinerary_service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "10:00",
                "origin": {"kind": "facility", "id": "spot_origin"},
            }
        ],
        must_visit=["spot_falls"],
    )
    assert not isinstance(planned, ItineraryToolError)
    assert planned.itinerary.version == 1
    assert "spot_shrine" not in [item.spot_id for item in planned.itinerary.days[0].items]

    tools = RealItineraryTools(
        recommendation_repository=MemoryRecommendationRepository(_recommendation_data()),
        itinerary_repository=itinerary_repository,
    )

    # --- ターンA: edit_itinerary(add) → done ---
    state_a = _state(
        utterance="鳥海山神社も追加して",
        itinerary=ItineraryState(itinerary=planned.itinerary, constraints=[], parent_version=None),
    )
    client_a = ScriptedMainAgentClient(
        [
            _turn_json(
                "edit_itinerary",
                {
                    "ops": [
                        {"op": "add", "targets": ["鳥海山神社"], "day": None, "after": None}
                    ],
                    "constraints": None,
                    "notes": None,
                },
            ),
            _turn_json("done", {}),
        ]
    )
    await run_main_agent(state_a, tools=tools, client=client_a)

    assert state_a.main_agent_failed is False
    assert state_a.trajectory[0].error is None
    assert state_a.itinerary.version == 2
    assert "spot_shrine" in [
        item.spot_id for item in state_a.itinerary.itinerary.days[0].items
    ]
    assert itinerary_repository.current_version == 2

    # --- ターンB: edit_itinerary(revert) → done(次ターンは DB から再読込した想定) ---
    current = await itinerary_repository.get_current(7)
    assert current is not None
    state_b = _state(
        utterance="元に戻して",
        itinerary=ItineraryState(
            itinerary=current.itinerary, constraints=current.constraints, parent_version=1
        ),
    )
    client_b = ScriptedMainAgentClient(
        [
            _turn_json(
                "edit_itinerary",
                {"ops": [{"op": "revert"}], "constraints": None, "notes": None},
            ),
            _turn_json("done", {}),
        ]
    )
    await run_main_agent(state_b, tools=tools, client=client_b)

    assert state_b.main_agent_failed is False
    assert state_b.trajectory[0].error is None
    assert state_b.itinerary.version == 1
    assert "spot_shrine" not in [
        item.spot_id for item in state_b.itinerary.itinerary.days[0].items
    ]
    assert itinerary_repository.current_version == 1
    # revert はソルバーを回さない(初回 plan + edit(add) の2回から増えない)。
    assert itinerary_repository.append_count == 2
