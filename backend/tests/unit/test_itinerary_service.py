"""Tool 相当ユースケースの pending 移送、編集、revert、エラー型を検証する。"""

from dataclasses import replace
from typing import Any

from app.domains.itinerary.repo_types import ItineraryVersion, PlanningData
from app.domains.itinerary.service import (
    ItineraryService,
    select_solution,
    select_solution_async,
)
from app.domains.itinerary.solver import (
    PlanningSpot,
    SolverConfig,
    TravelTimeMatrix,
    itinerary_spot_ids,
)
from app.domains.itinerary.types import Constraint, Itinerary, ToolError, ToolErrorCode


class MemoryItineraryRepository:
    def __init__(self, planning: PlanningData) -> None:
        self.planning = planning
        self.pending: list[dict[str, Any]] = []
        self.versions: dict[int, ItineraryVersion] = {}
        self.current_version: int | None = None
        self.append_count = 0

    async def get_current(
        self, user_id: int, *, for_update: bool = False
    ) -> ItineraryVersion | None:
        if self.current_version is None:
            return None
        return self.versions[self.current_version]

    async def load_planning_data(self) -> PlanningData:
        return self.planning

    async def get_pending_constraints(
        self, user_id: int, *, for_update: bool = False
    ) -> list[dict[str, Any]]:
        return [dict(value) for value in self.pending]

    async def clear_pending_constraints(self, user_id: int) -> None:
        self.pending = []

    async def append_version(
        self,
        *,
        user_id: int,
        itinerary,
        constraints,
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
            value.model_dump(mode="json") if isinstance(value, Constraint) else dict(value)
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


def _planning() -> PlanningData:
    spots = {
        "spot_origin": PlanningSpot("spot_origin", ("宿泊施設",), 15, kind="facility"),
        "spot_a": PlanningSpot("spot_a", ("滝",), 30),
        "spot_b": PlanningSpot("spot_b", ("神社",), 30),
        "spot_c": PlanningSpot("spot_c", ("自然",), 30),
        "spot_d": PlanningSpot("spot_d", ("温泉",), 30),
    }
    legs = {
        (source, target, "car"): 10 * 60 for source in spots for target in spots if source != target
    }
    return PlanningData(spots=spots, travel_times=TravelTimeMatrix(legs))


def _planning_with_spare_capacity() -> PlanningData:
    """6 件の POI に対して 4 件分の予算しか無い規模(refill 検証用)。

    どの 2 地点間も一律 20 分の車移動にしているので、k 件を回る所要は
    (k+1)*20(移動)+ k*30(滞在)分になる。240 分の日予算では k=4 が
    ちょうど収まり(220分)、k=5 は収まらない(270分)。したがって初回計画は
    必ず 6 件中 4 件を選び、残り 2 件が「空き」として残る。
    """

    spots = {
        "spot_origin": PlanningSpot("spot_origin", ("宿泊施設",), 15, kind="facility"),
        "spot_a": PlanningSpot("spot_a", ("滝",), 30),
        "spot_b": PlanningSpot("spot_b", ("神社",), 30),
        "spot_c": PlanningSpot("spot_c", ("自然",), 30),
        "spot_d": PlanningSpot("spot_d", ("温泉",), 30),
        "spot_e": PlanningSpot("spot_e", ("公園",), 30),
        "spot_f": PlanningSpot("spot_f", ("海岸",), 30),
    }
    legs = {
        (source, target, "car"): 20 * 60
        for source in spots
        for target in spots
        if source != target
    }
    return PlanningData(spots=spots, travel_times=TravelTimeMatrix(legs))


def _service(repository: MemoryItineraryRepository) -> ItineraryService:
    return ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(
            iterations=20,
            minimum_iterations=20,
            time_limit_ms=2_000,
        ),
    )


async def test_first_plan_moves_pending_constraints_and_returns_two_alternatives() -> None:
    repository = MemoryItineraryRepository(_planning())
    repository.pending = [
        {
            "id": "pending_9",
            "pred": "not_consecutive",
            "args": {"target": "神社"},
            "source_text": "神社を続けない",
        }
    ]
    service = _service(repository)

    result = await service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "13:00",
                "origin": {"kind": "spot", "id": "spot_origin"},
            }
        ],
        must_visit=["spot_a"],
        constraints=[{"pred": "future_pred", "args": {}}],
        utilities={"spot_d": -10},
    )

    assert not isinstance(result, ToolError)
    assert result.itinerary.version == 1
    assert len(result.alternatives) == 2
    assert result.selection_used is False
    assert "spot_a" in result.spot_ids
    assert result.unmodeled[0].pred == "future_pred"
    assert repository.pending == []
    ids = [value["id"] for value in repository.versions[1].constraints]
    assert ids == ["c_001", "c_002"]
    # 2026-08-04 レビュー是正(裁定7): `result.constraints` は DB へ保存された
    # ものと一致する(呼び出し元がこれを正として state を組み立てるため)。
    assert [value.id for value in result.constraints] == ids


async def test_edit_copies_constraint_ids_adds_version_and_revert_writes_no_row() -> None:
    repository = MemoryItineraryRepository(_planning())
    service = _service(repository)
    planned = await service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": 540,
                "end": 720,
                "origin": {"kind": "facility", "id": "spot_origin"},
            }
        ],
        must_visit=["spot_a"],
        utilities={"spot_d": -10},
    )
    assert not isinstance(planned, ToolError)
    assert "spot_d" not in planned.spot_ids
    original_constraint_id = repository.versions[1].constraints[0]["id"]

    edited = await service.edit_itinerary(
        user_id=7,
        ops=[{"op": "add", "targets": ["spot_d"], "after": "spot_a"}],
        utilities={"spot_d": -10},
    )

    assert not isinstance(edited, ToolError)
    assert edited.itinerary.version == 2
    assert "spot_d" in itinerary_spot_ids(edited.itinerary)
    assert repository.versions[2].parent_version == 1
    assert repository.versions[2].constraints[0]["id"] == original_constraint_id
    assert len(repository.versions[2].constraints) == 2
    assert len(edited.diff.moved) <= 2
    # 2026-08-04 レビュー是正(裁定7): edit 時の暗黙制約(add op由来の require)
    # も含め、`result.constraints` が保存内容と一致する。
    assert [value.id for value in edited.constraints] == [
        value["id"] for value in repository.versions[2].constraints
    ]
    append_count = repository.append_count

    reverted = await service.edit_itinerary(
        user_id=7,
        ops=[
            {"op": "add", "targets": "$99.spot_ids"},
            {"op": "revert"},
        ],
    )

    assert not isinstance(reverted, ToolError)
    assert reverted.itinerary.version == 1
    assert repository.append_count == append_count
    assert sorted(repository.versions) == [1, 2]
    # revert 先(v1)の制約がそのまま返る(§5「revert では対象版の制約へ戻す」)。
    assert [value.id for value in reverted.constraints] == [
        value["id"] for value in repository.versions[1].constraints
    ]


async def test_edit_without_itinerary_returns_recoverable_precondition_error() -> None:
    result = await _service(MemoryItineraryRepository(_planning())).edit_itinerary(
        user_id=7,
        ops=[{"op": "remove", "targets": ["spot_a"]}],
    )

    assert isinstance(result, ToolError)
    assert result.code is ToolErrorCode.PRECONDITION_UNMET
    assert result.recoverable is True


def test_solution_selection_hook_defaults_to_a() -> None:
    # 型だけを確認する小さな候補。selector 未接続なら自由文に関係なく A。
    solution_a = Itinerary(days=[], version=1)
    solution_b = Itinerary(days=[], version=2)

    assert select_solution([solution_a, solution_b], "のんびり") == solution_a


async def test_solution_selection_hook_accepts_async_selector() -> None:
    solution_a = Itinerary(days=[], version=1)
    solution_b = Itinerary(days=[], version=2)

    async def choose_b(
        solutions: list[Itinerary],
        free_text: str,
    ) -> Itinerary:
        assert free_text == "温泉を優先"
        return solutions[1]

    selected = await select_solution_async(
        [solution_a, solution_b],
        "温泉を優先",
        selector=choose_b,
    )

    assert selected == solution_b
    assert selected is not solution_b


async def test_plan_emits_solution_a_before_async_selection() -> None:
    repository = MemoryItineraryRepository(_planning())
    order: list[str] = []

    async def provisional(itinerary: Itinerary, diff: Any) -> None:
        assert itinerary.version == 1
        assert diff.added == []
        order.append("provisional")

    async def choose_b(
        solutions: list[Itinerary],
        free_text: str,
    ) -> Itinerary:
        del free_text
        order.append("selector")
        return solutions[1]

    service = ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(
            iterations=20,
            minimum_iterations=20,
            time_limit_ms=2_000,
        ),
        selector=choose_b,
        provisional_sink=provisional,
    )

    result = await service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "13:00",
                "origin": {"kind": "spot", "id": "spot_origin"},
            }
        ],
        selection_text="のんびり",
    )

    assert not isinstance(result, ToolError)
    assert order == ["provisional", "selector"]
    assert result.selection_used is True


async def _planned_with_spare_capacity() -> tuple[MemoryItineraryRepository, Any]:
    """初回計画は selector 無し(解 A 決定的)で作る。

    選択の有無を検証したいのは edit 呼び出し側なので、初回計画に selector を
    付けると解 B(diversity 版)が選ばれて件数の前提が崩れる。
    """

    repository = MemoryItineraryRepository(_planning_with_spare_capacity())
    plan_service = ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(iterations=60, minimum_iterations=60, time_limit_ms=2_000),
    )
    planned = await plan_service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "13:00",
                "origin": {"kind": "spot", "id": "spot_origin"},
            }
        ],
    )
    assert not isinstance(planned, ToolError)
    return repository, planned


async def test_edit_default_allow_refill_false_locks_visit_set_and_skips_selection() -> None:
    """ADR-0021: 既定(allow_refill=False)は remove op の対象以外の訪問が

    維持され、空いた時間に新規スポットが入らない。解 A/B/C の生成と LLM
    選択も省略される(selector が呼ばれない)。
    """

    calls: list[str] = []

    async def choose_b(solutions: list[Itinerary], free_text: str) -> Itinerary:
        calls.append(free_text)
        return solutions[1]

    repository, planned = await _planned_with_spare_capacity()
    initial_ids = frozenset(planned.spot_ids)
    # 6 件中ちょうど 4 件だけが予算に収まる規模にしてある(空きがある)。
    assert len(initial_ids) == 4
    removed = sorted(initial_ids)[0]

    edit_service = ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(iterations=60, minimum_iterations=60, time_limit_ms=2_000),
        selector=choose_b,
    )
    edited = await edit_service.edit_itinerary(
        user_id=7,
        ops=[{"op": "remove", "targets": [removed]}],
        # allow_refill=False では使われないはずのヒント。
        selection_text="もっと詰め込みたい",
    )

    assert not isinstance(edited, ToolError)
    assert frozenset(edited.spot_ids) == initial_ids - {removed}
    assert edited.alternatives == []
    assert edited.selection_used is False
    assert calls == []  # selector は呼ばれていない


async def test_edit_allow_refill_true_can_refill_and_uses_selection() -> None:
    """ADR-0021: allow_refill=True では従来どおりフル ILS + A/B/C + LLM 選択。

    空いた時間に(除去した以外の)新規スポットが入り得る。
    """

    calls: list[str] = []

    async def choose_b(solutions: list[Itinerary], free_text: str) -> Itinerary:
        calls.append(free_text)
        return solutions[1]

    repository, planned = await _planned_with_spare_capacity()
    initial_ids = frozenset(planned.spot_ids)
    assert len(initial_ids) == 4
    removed = sorted(initial_ids)[0]

    edit_service = ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(iterations=60, minimum_iterations=60, time_limit_ms=2_000),
        selector=choose_b,
    )
    edited = await edit_service.edit_itinerary(
        user_id=7,
        ops=[{"op": "remove", "targets": [removed]}],
        allow_refill=True,
        selection_text="もっと詰め込みたい",
    )

    assert not isinstance(edited, ToolError)
    assert frozenset(edited.spot_ids) != initial_ids - {removed}
    assert edited.selection_used is True
    assert len(edited.alternatives) == 2
    assert calls == ["もっと詰め込みたい"]  # selector が 1 回呼ばれた


async def test_plan_itinerary_sets_assumptions_on_result_and_provisional() -> None:
    repository = MemoryItineraryRepository(_planning())
    provisional_assumptions: list[list[str]] = []

    async def provisional(itinerary: Itinerary, diff: Any) -> None:
        del diff
        provisional_assumptions.append(list(itinerary.assumptions))

    service = ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(iterations=20, minimum_iterations=20, time_limit_ms=2_000),
        provisional_sink=provisional,
    )

    result = await service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "13:00",
                "origin": {"kind": "spot", "id": "spot_origin"},
            }
        ],
        assumptions=["日付は明日と仮定"],
    )

    assert not isinstance(result, ToolError)
    assert result.itinerary.assumptions == ["日付は明日と仮定"]
    assert provisional_assumptions == [["日付は明日と仮定"]]
    assert repository.versions[1].itinerary.assumptions == ["日付は明日と仮定"]


async def test_edit_itinerary_copies_assumptions_by_default_and_replaces_when_given() -> None:
    """ADR-0021: `assumptions=None` は基の版からそのままコピー、リストを

    与えたら置換する。
    """

    repository = MemoryItineraryRepository(_planning())
    service = _service(repository)
    planned = await service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "13:00",
                "origin": {"kind": "spot", "id": "spot_origin"},
            }
        ],
        must_visit=["spot_a"],
        assumptions=["起点は未確認"],
    )
    assert not isinstance(planned, ToolError)
    assert planned.itinerary.assumptions == ["起点は未確認"]

    copied = await service.edit_itinerary(
        user_id=7,
        ops=[{"op": "lock", "targets": ["spot_a"], "locked": True}],
    )
    assert not isinstance(copied, ToolError)
    assert copied.itinerary.assumptions == ["起点は未確認"]

    replaced = await service.edit_itinerary(
        user_id=7,
        ops=[{"op": "lock", "targets": ["spot_a"], "locked": False}],
        assumptions=[],
    )
    assert not isinstance(replaced, ToolError)
    assert replaced.itinerary.assumptions == []


async def test_edit_default_allow_refill_false_still_applies_explicit_add_op() -> None:
    """レビュー是正(項目7): allow_refill=False(既定)でも、ops による

    明示的な add は反映される。「ソルバーが自動で詰め直す」(refill)のとは
    別物であることの確認テスト — ops の明示追加は常に反映されるべきで、
    ADR-0021 が禁じているのは**ソルバーが自発的に**新規スポットを選ぶこと
    だけである。
    """

    repository = MemoryItineraryRepository(_planning())
    plan_service = ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(iterations=20, minimum_iterations=20, time_limit_ms=2_000),
    )
    planned = await plan_service.plan_itinerary(
        user_id=7,
        days=[
            {
                "date": "2026-08-10",
                "start": "09:00",
                "end": "13:00",
                "origin": {"kind": "spot", "id": "spot_origin"},
            }
        ],
        must_visit=["spot_a"],
        # b/c/d を強く忌避させ、初回計画では spot_a だけが選ばれる規模にする
        # (add op で明示追加した spot_b だけが増えたことを明確にするため)。
        utilities={"spot_b": -10, "spot_c": -10, "spot_d": -10},
    )
    assert not isinstance(planned, ToolError)
    assert frozenset(planned.spot_ids) == frozenset({"spot_a"})

    edit_service = ItineraryService(
        repository,  # type: ignore[arg-type]
        solver_config=SolverConfig(iterations=20, minimum_iterations=20, time_limit_ms=2_000),
    )
    edited = await edit_service.edit_itinerary(
        user_id=7,
        ops=[{"op": "add", "targets": ["spot_b"]}],
        # allow_refill は既定(False)のまま。
    )

    assert not isinstance(edited, ToolError)
    # 明示的に add した spot_b は反映される。ソルバーが自発的に選んだのでは
    # ない spot_c/spot_d は増えない(集合固定)。
    assert frozenset(edited.spot_ids) == frozenset({"spot_a", "spot_b"})
