"""旅程の初回計画、編集、解選択、永続化を束ねるユースケース。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from app.domains.itinerary.ops import (
    OpApplicationError,
    apply_ops,
    calculate_diff,
    find_revert,
)
from app.domains.itinerary.predicates import normalize_constraints
from app.domains.itinerary.repo_types import (
    ItineraryNotFoundError,
    ItineraryVersionConflictError,
    PlanningData,
)
from app.domains.itinerary.solver import (
    PlanningDay,
    SolverConfig,
    SolverInput,
    itinerary_spot_ids,
    solve_itinerary,
    validate_hard_constraints,
)
from app.domains.itinerary.types import (
    Constraint,
    Diff,
    EditItineraryResult,
    Itinerary,
    Mode,
    Op,
    PlanItineraryResult,
    ToolError,
    ToolErrorCode,
    parse_minute,
)

if TYPE_CHECKING:
    from app.domains.itinerary.repo import ItineraryRepository


class SolutionSelector(Protocol):
    def __call__(
        self,
        solutions: Sequence[Itinerary],
        free_text: str,
    ) -> Itinerary | Awaitable[Itinerary]: ...


class LegRouteProvider(Protocol):
    async def route_id_for_leg(self, source: str, target: str, mode: Mode) -> str: ...


class ProvisionalItinerarySink(Protocol):
    def __call__(
        self,
        itinerary: Itinerary,
        diff: Diff,
    ) -> None | Awaitable[None]: ...


UtilityInput = Mapping[str, float] | Callable[[Any], float]


class ItineraryService:
    def __init__(
        self,
        repository: ItineraryRepository,
        *,
        solver_config: SolverConfig | None = None,
        selector: SolutionSelector | None = None,
        route_provider: LegRouteProvider | None = None,
        provisional_sink: ProvisionalItinerarySink | None = None,
    ) -> None:
        self.repository = repository
        self.solver_config = solver_config or SolverConfig()
        self.selector = selector
        self.route_provider = route_provider
        self.provisional_sink = provisional_sink

    async def plan_itinerary(
        self,
        *,
        user_id: int,
        days: Sequence[PlanningDay | Mapping[str, Any]],
        must_visit: Sequence[str] = (),
        constraints: Sequence[Constraint | Mapping[str, Any]] = (),
        utilities: UtilityInput | None = None,
        selection_text: str = "",
        created_by_message_id: int | None = None,
        assumptions: Sequence[str] = (),
    ) -> PlanItineraryResult | ToolError:
        if await self.repository.get_current(user_id) is not None:
            return ToolError(
                code=ToolErrorCode.PRECONDITION_UNMET,
                message_ja="既に旅程があります。変更には旅程編集を使ってください。",
                recoverable=True,
                details={"user_id": user_id},
            )
        try:
            planning = await self.repository.load_planning_data()
            parsed_days = _parse_days(days, planning)
        except ValueError as exc:
            return _reference_error(exc)
        # for_update=True は渡さない(ItineraryRepository.get_pending_constraints
        # の docstring のとおり、ターン中の threads 行ロックは自己デッドロックの
        # 原因になるため撤去した。2026-08-04 レビュー是正)。
        pending = await self.repository.get_pending_constraints(user_id)
        # pending の id は旅程外の仮番号なので、v1 へ移すときに必ず採番し直す。
        pending_without_ids = [
            {key: value for key, value in raw.items() if key != "id"} for raw in pending
        ]
        raw_constraints: list[Constraint | Mapping[str, Any]] = [
            *pending_without_ids,
            *constraints,
        ]
        for spot_id in must_visit:
            raw_constraints.append(
                {
                    "pred": "require",
                    "args": {"target": spot_id},
                    "source_text": "旅程作成時の must_visit",
                }
            )
        normalized = normalize_constraints(
            raw_constraints,
            planning.spots,
            created_at_version=1,
        )
        try:
            utility_values = _utilities(planning, utilities)
            solver_input = SolverInput(
                days=tuple(parsed_days),
                spots=planning.spots,
                travel_times=planning.travel_times,
                constraints=tuple(normalized.constraints),
                utilities=utility_values,
                required_spot_ids=frozenset(must_visit),
                config=self.solver_config,
            )
            solved = solve_itinerary(solver_input)
            resolved_assumptions = list(assumptions)
            await _emit_provisional(
                self.provisional_sink,
                solved.solutions[0].model_copy(
                    update={"version": 1, "assumptions": resolved_assumptions}, deep=True
                ),
                Diff(),
            )
            (
                selected,
                alternatives,
                selection_used,
            ) = await _select_solution_with_alternatives(
                solved.solutions,
                selection_text,
                selector=self.selector,
            )
            selected = selected.model_copy(
                update={"assumptions": resolved_assumptions}, deep=True
            )
        except ValueError as exc:
            return _reference_error(exc)
        if self.route_provider is not None:
            selected = await _attach_route_ids(selected, self.route_provider)
        hard_errors = validate_hard_constraints(
            selected,
            planning.spots,
            planning.travel_times,
        )
        if hard_errors:
            return _hard_constraint_error(hard_errors)
        try:
            stored = await self.repository.append_version(
                user_id=user_id,
                itinerary=selected,
                constraints=normalized.constraints,
                origin="plan",
                created_by_message_id=created_by_message_id,
            )
            await self.repository.clear_pending_constraints(user_id)
        except (ItineraryNotFoundError, ItineraryVersionConflictError) as exc:
            return _precondition_error(exc)
        final = stored.itinerary
        versioned_alternatives = [
            alternative.model_copy(update={"version": stored.version}, deep=True)
            for alternative in alternatives
        ]
        return PlanItineraryResult(
            itinerary=final,
            alternatives=versioned_alternatives,
            concessions=final.concessions,
            selection_used=selection_used,
            spot_ids=itinerary_spot_ids(final),
            unmodeled=normalized.unmodeled,
            constraints=[Constraint.model_validate(value) for value in stored.constraints],
        )

    async def edit_itinerary(
        self,
        *,
        user_id: int,
        ops: Sequence[Op | Mapping[str, Any]],
        constraints: Sequence[Constraint | Mapping[str, Any]] = (),
        constraints_remove: Sequence[str] = (),
        utilities: UtilityInput | None = None,
        selection_text: str = "",
        created_by_message_id: int | None = None,
        allow_refill: bool = False,
        assumptions: Sequence[str] | None = None,
    ) -> EditItineraryResult | ToolError:
        current = await self.repository.get_current(user_id)
        if current is None:
            return ToolError(
                code=ToolErrorCode.PRECONDITION_UNMET,
                message_ja="編集できる旅程がまだありません。先に旅程を作成してください。",
                recoverable=True,
                details={"user_id": user_id},
            )
        try:
            revert = find_revert(ops)
        except ValidationError as exc:
            return _reference_error(exc)
        if revert is not None:
            try:
                reverted = await self.repository.revert(
                    user_id,
                    to_version=revert.to_version,
                )
            except (ItineraryNotFoundError, ItineraryVersionConflictError) as exc:
                return _precondition_error(exc)
            return EditItineraryResult(
                itinerary=reverted.itinerary,
                alternatives=[],
                concessions=reverted.itinerary.concessions,
                diff=calculate_diff(current.itinerary, reverted.itinerary),
                selection_used=False,
                spot_ids=itinerary_spot_ids(reverted.itinerary),
                unmodeled=[],
                # revert 先の版の制約(§5「revert では対象版の制約へ戻す」)を
                # そのまま返す。2026-08-04 レビュー是正・裁定7。
                constraints=[
                    Constraint.model_validate(value) for value in reverted.constraints
                ],
            )
        planning = await self.repository.load_planning_data()
        try:
            applied = apply_ops(
                current.itinerary,
                list(ops),
                known_spot_ids=set(planning.spots),
                default_stays={spot_id: spot.stay_min for spot_id, spot in planning.spots.items()},
            )
        except (OpApplicationError, ValidationError, ValueError) as exc:
            return _reference_error(exc)
        existing_ids = {str(raw.get("id")) for raw in current.constraints}
        missing_remove_ids = set(constraints_remove) - existing_ids
        if missing_remove_ids:
            return _reference_error(
                ValueError(f"実在しない制約 id です: {sorted(missing_remove_ids)}")
            )
        existing_raw = [
            raw for raw in current.constraints if str(raw.get("id")) not in set(constraints_remove)
        ]
        existing = normalize_constraints(
            existing_raw,
            planning.spots,
            created_at_version=current.version,
        )
        used_ids = {constraint.id for constraint in existing.constraints}
        added = normalize_constraints(
            constraints,
            planning.spots,
            created_at_version=current.version + 1,
            used_ids=used_ids,
        )
        operation_raw: list[Mapping[str, Any]] = []
        for spot_id in sorted(applied.required_spot_ids):
            operation_raw.append(
                {
                    "pred": "require",
                    "args": {"target": spot_id},
                    "source_text": "add/replace op",
                }
            )
        for spot_id, (arrive, depart) in sorted(applied.preferred_windows.items()):
            operation_raw.append(
                {
                    "pred": "time_window",
                    "args": {"target": spot_id, "from": arrive, "to": depart},
                    "weight": 0.25,
                    "source_text": "set_time op",
                }
            )
        operation_constraints = normalize_constraints(
            operation_raw,
            planning.spots,
            created_at_version=current.version + 1,
            used_ids={*used_ids, *(constraint.id for constraint in added.constraints)},
        )
        persisted_constraints = [
            *existing.constraints,
            *added.constraints,
            *operation_constraints.constraints,
        ]
        # ADR-0021: 編集ターンの既定(allow_refill=False)は訪問集合を
        # ops 適用後のまま固定する。ops 適用後の全訪問を
        # `removal_protected_spot_ids`(削除保護専用。2026-08-04 レビュー
        # 是正・C-1)へ入れて shake / 実行可能化フォールバックの除去対象から
        # 外し、insertion_pool を空にして新規スポットの挿入候補をゼロにする。
        # `protected_spot_ids`(ops が触れた項目由来の削除保護)は
        # allow_refill に関わらずそのまま渡す — こちらを流用すると
        # `_two_opt`/`_or_opt` の並び替えまで止まってしまう(採らなかった案
        # 「全項目 locked 扱い」と同じ過剰制約になっていた実装バグ)。
        # ソルバーがやるのは並び・時刻の再調整と経路の引き直しだけになる。
        # allow_refill=True のときは従来どおりフル ILS(制限なし)。
        post_ops_spot_ids = frozenset(itinerary_spot_ids(applied.itinerary))
        removal_protected_spot_ids = frozenset() if allow_refill else post_ops_spot_ids
        insertion_pool: frozenset[str] | None = None if allow_refill else frozenset()
        solver_input = SolverInput(
            days=tuple(_days_from_itinerary(applied.itinerary)),
            spots=planning.spots,
            travel_times=planning.travel_times,
            constraints=tuple(persisted_constraints),
            utilities=_utilities(planning, utilities),
            previous=current.itinerary,
            initial=applied.itinerary,
            required_spot_ids=applied.required_spot_ids,
            excluded_spot_ids=applied.excluded_spot_ids,
            protected_spot_ids=applied.protected_spot_ids,
            removal_protected_spot_ids=removal_protected_spot_ids,
            insertion_pool=insertion_pool,
            stay_overrides=applied.stay_overrides,
            config=self.solver_config,
        )
        # 集合固定(allow_refill=False)では解 A/B/C の生成そのものを省略し
        # (`alternatives=False`)、単一解を返す。
        solved = solve_itinerary(solver_input, alternatives=allow_refill)
        resolved_assumptions = (
            list(assumptions) if assumptions is not None else list(current.itinerary.assumptions)
        )
        provisional = solved.solutions[0].model_copy(
            update={"version": current.version + 1, "assumptions": resolved_assumptions},
            deep=True,
        )
        await _emit_provisional(
            self.provisional_sink,
            provisional,
            calculate_diff(current.itinerary, provisional),
        )
        if allow_refill:
            (
                selected,
                alternatives,
                selection_used,
            ) = await _select_solution_with_alternatives(
                solved.solutions,
                selection_text,
                selector=self.selector,
            )
        else:
            # ADR-0021: 集合固定では解の多様化と LLM 選択(§4.4 経路3)を
            # 省略し、解 A をそのまま単一解として使う(編集ターンの LLM
            # 呼び出しが 1 回減る)。
            selected = solved.solutions[0]
            alternatives = []
            selection_used = False
        selected = selected.model_copy(update={"assumptions": resolved_assumptions}, deep=True)
        if self.route_provider is not None:
            selected = await _attach_route_ids(selected, self.route_provider)
        hard_errors = validate_hard_constraints(
            selected,
            planning.spots,
            planning.travel_times,
        )
        if hard_errors:
            return _hard_constraint_error(hard_errors)
        try:
            stored = await self.repository.append_version(
                user_id=user_id,
                itinerary=selected,
                constraints=persisted_constraints,
                origin="edit",
                created_by_message_id=created_by_message_id,
                expected_parent_version=current.version,
            )
        except (ItineraryNotFoundError, ItineraryVersionConflictError) as exc:
            return _precondition_error(exc)
        final = stored.itinerary
        return EditItineraryResult(
            itinerary=final,
            alternatives=[
                alternative.model_copy(update={"version": stored.version}, deep=True)
                for alternative in alternatives
            ],
            concessions=final.concessions,
            diff=calculate_diff(current.itinerary, final),
            selection_used=selection_used,
            spot_ids=itinerary_spot_ids(final),
            unmodeled=[
                *existing.unmodeled,
                *added.unmodeled,
                *operation_constraints.unmodeled,
            ],
            constraints=[Constraint.model_validate(value) for value in stored.constraints],
        )


def select_solution(
    solutions: Sequence[Itinerary],
    free_text: str,
    *,
    selector: SolutionSelector | None = None,
) -> Itinerary:
    """LLM 選択の差し込み口。未接続時は必ず解 A を選ぶ。"""

    if not solutions:
        raise ValueError("選択できる旅程解がありません")
    if selector is None:
        return solutions[0].model_copy(deep=True)
    if inspect.iscoroutinefunction(selector):
        raise TypeError("async selector は select_solution_async で呼んでください")
    selected = selector(solutions, free_text)
    if inspect.isawaitable(selected):
        if inspect.iscoroutine(selected):
            selected.close()
        raise TypeError("async selector は select_solution_async で呼んでください")
    return _validated_selection(solutions, selected)


async def select_solution_async(
    solutions: Sequence[Itinerary],
    free_text: str,
    *,
    selector: SolutionSelector | None = None,
) -> Itinerary:
    """同期・非同期どちらの selector もイベントループを塞がず接続する。"""

    if not solutions:
        raise ValueError("選択できる旅程解がありません")
    if selector is None:
        return solutions[0].model_copy(deep=True)
    selected = selector(solutions, free_text)
    if inspect.isawaitable(selected):
        selected = await selected
    return _validated_selection(solutions, selected)


def _validated_selection(
    solutions: Sequence[Itinerary],
    selected: Itinerary,
) -> Itinerary:
    if selected not in solutions:
        raise ValueError("selector が候補外の旅程を返しました")
    return selected.model_copy(deep=True)


async def _select_solution_with_alternatives(
    solutions: Sequence[Itinerary],
    free_text: str,
    *,
    selector: SolutionSelector | None,
) -> tuple[Itinerary, list[Itinerary], bool]:
    selected = await select_solution_async(
        solutions,
        free_text,
        selector=selector,
    )
    selected_index = next(
        index for index, solution in enumerate(solutions) if solution == selected
    )
    return (
        selected,
        [
            solution.model_copy(deep=True)
            for index, solution in enumerate(solutions)
            if index != selected_index
        ],
        selector is not None,
    )


async def _emit_provisional(
    sink: ProvisionalItinerarySink | None,
    itinerary: Itinerary,
    diff: Diff,
) -> None:
    if sink is None:
        return
    result = sink(itinerary.model_copy(deep=True), diff.model_copy(deep=True))
    if inspect.isawaitable(result):
        await result


async def plan_itinerary(
    repository: ItineraryRepository,
    **kwargs: Any,
) -> PlanItineraryResult | ToolError:
    return await ItineraryService(repository).plan_itinerary(**kwargs)


async def edit_itinerary(
    repository: ItineraryRepository,
    **kwargs: Any,
) -> EditItineraryResult | ToolError:
    return await ItineraryService(repository).edit_itinerary(**kwargs)


def _parse_days(
    values: Sequence[PlanningDay | Mapping[str, Any]], planning: PlanningData
) -> list[PlanningDay]:
    parsed: list[PlanningDay] = []
    for value in values:
        if isinstance(value, PlanningDay):
            day = value
        else:
            raw = dict(value)
            origin = _spot_endpoint(raw.get("origin"), "origin")
            destination = _spot_endpoint(raw.get("destination") or raw.get("origin"), "destination")
            day = PlanningDay(
                date=str(raw.get("date", "")),
                start_min=parse_minute(raw.get("start_min", raw.get("start")), field_name="start"),
                end_min=parse_minute(raw.get("end_min", raw.get("end")), field_name="end"),
                origin_spot_id=origin,
                destination_spot_id=destination,
            )
        if day.origin_spot_id not in planning.spots:
            raise ValueError(f"起点の spot_id が実在しません: {day.origin_spot_id}")
        if day.destination_spot_id not in planning.spots:
            raise ValueError(f"終点の spot_id が実在しません: {day.destination_spot_id}")
        if day.start_min > day.end_min:
            raise ValueError("日の start は end 以下にしてください")
        parsed.append(day)
    if not parsed:
        raise ValueError("days は 1 日以上必要です")
    return parsed


def _spot_endpoint(value: Any, field_name: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} は spot/facility の指定にしてください")
    kind = value.get("kind")
    if kind not in {"spot", "facility"}:
        raise ValueError(
            f"{field_name} の coord は静的移動時間行列で解決できません。spot_id を指定してください"
        )
    spot_id = value.get("spot_id", value.get("id"))
    if not isinstance(spot_id, str) or not spot_id:
        raise ValueError(f"{field_name}.id がありません")
    return spot_id


def _days_from_itinerary(itinerary: Itinerary) -> list[PlanningDay]:
    return [
        PlanningDay(
            date=day.date,
            start_min=day.start_min,
            end_min=day.end_min,
            origin_spot_id=day.origin.spot_id,
            destination_spot_id=day.destination.spot_id,
        )
        for day in itinerary.days
    ]


def _utilities(planning: PlanningData, values: UtilityInput | None) -> dict[str, float]:
    if values is None:
        return {}
    if callable(values):
        return {spot_id: float(values(spot)) for spot_id, spot in planning.spots.items()}
    unknown = set(values) - set(planning.spots)
    if unknown:
        raise ValueError(f"utility に実在しない spot_id があります: {sorted(unknown)}")
    return {spot_id: float(value) for spot_id, value in values.items()}


async def _attach_route_ids(itinerary: Itinerary, provider: LegRouteProvider) -> Itinerary:
    result = itinerary.model_copy(deep=True)
    requests: list[tuple[int, int, str, str, Mode]] = []
    for day_index, day in enumerate(result.days):
        source = day.origin.spot_id
        for item_index, item in enumerate(day.items):
            requests.append((day_index, item_index, source, item.spot_id, item.leg_from_prev.mode))
            source = item.spot_id
    route_ids = await asyncio.gather(
        *(
            provider.route_id_for_leg(source, target, mode)
            for _, _, source, target, mode in requests
        )
    )
    for (day_index, item_index, _, _, _), route_id in zip(requests, route_ids, strict=True):
        item = result.days[day_index].items[item_index]
        result.days[day_index].items[item_index] = item.model_copy(
            update={"leg_from_prev": item.leg_from_prev.model_copy(update={"route_id": route_id})}
        )
    return result


def _reference_error(exc: Exception) -> ToolError:
    return ToolError(
        code=ToolErrorCode.REFERENCE_UNRESOLVED,
        message_ja=str(exc),
        recoverable=True,
        details={"error_type": type(exc).__name__},
    )


def _precondition_error(exc: Exception) -> ToolError:
    return ToolError(
        code=ToolErrorCode.PRECONDITION_UNMET,
        message_ja=str(exc),
        recoverable=True,
        details={"error_type": type(exc).__name__},
    )


def _hard_constraint_error(errors: list[str]) -> ToolError:
    return ToolError(
        code=ToolErrorCode.INTERNAL,
        message_ja="物理的に整合する旅程を確定できませんでした。",
        recoverable=False,
        details={"hard_constraint_errors": errors},
    )
