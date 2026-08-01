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
