"""TOPTW を固定シードの ILS で解く、依存ゼロの旅程ソルバー。"""

from __future__ import annotations

import logging
import math
import random
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from functools import lru_cache
from typing import Any, TypeAlias

from app.domains.itinerary.predicates import (
    evaluate_constraints,
    target_spot_ids,
)
from app.domains.itinerary.types import (
    Constraint,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    LegFromPrev,
    Mode,
    PredEnum,
    SpotEndpoint,
)

logger = logging.getLogger(__name__)

UtilityFunction: TypeAlias = Callable[["PlanningSpot"], float]


@dataclass(frozen=True, slots=True)
class PlanningSpot:
    spot_id: str
    tags_ja: tuple[str, ...]
    stay_min: int
    open_hours: Any = None
    season_closed_months: tuple[int, ...] = ()
    kind: str = "poi"


@dataclass(frozen=True, slots=True)
class PlanningDay:
    date: str
    start_min: int
    end_min: int
    origin_spot_id: str
    destination_spot_id: str


@dataclass(frozen=True, slots=True)
class TravelLeg:
    mode: Mode
    min: int


class TravelTimeMatrix:
    """秒の DB 値を切り上げ分へ変換し、存在する行だけを保持する。"""

    def __init__(self, legs: Mapping[tuple[str, str, Mode | str], int]) -> None:
        self._legs: dict[tuple[str, str, Mode], int] = {}
        by_pair: dict[tuple[str, str], list[TravelLeg]] = {}
        self._logged_missing: set[tuple[str, str]] = set()
        for (source, target, mode_value), duration_sec in legs.items():
            mode = mode_value if isinstance(mode_value, Mode) else Mode(mode_value)
            if duration_sec < 0:
                raise ValueError("移動時間は 0 秒以上にしてください")
            minutes = math.ceil(duration_sec / 60)
            self._legs[(source, target, mode)] = minutes
            by_pair.setdefault((source, target), []).append(TravelLeg(mode, minutes))
        self._modes = {
            pair: tuple(sorted(values, key=lambda value: (value.min, value.mode.value)))
            for pair, values in by_pair.items()
        }
        self._same_spot = (TravelLeg(Mode.CAR, 0),)

    def modes(self, source: str, target: str) -> tuple[TravelLeg, ...]:
        if source == target:
            return self._same_spot
        return self._modes.get((source, target), ())

    def best_leg(
        self,
        source: str,
        target: str,
        *,
        preferred_mode: Mode | None = None,
        log_missing: bool = False,
    ) -> TravelLeg | None:
        values = self.modes(source, target)
        if preferred_mode is not None:
            preferred = next((leg for leg in values if leg.mode is preferred_mode), None)
            if preferred is not None:
                return preferred
        if values:
            return values[0]
        if log_missing and (source, target) not in self._logged_missing:
            self._logged_missing.add((source, target))
            logger.warning(
                "itinerary_travel_time_missing",
                extra={
                    "degraded": True,
                    "from_spot_id": source,
                    "to_spot_id": target,
                },
            )
        return None


@dataclass(frozen=True, slots=True)
class SolverConfig:
    # 43 地点の厳密解比較で決めた値。詳細はテストと最終報告を参照。
    iterations: int = 160
    shake_min: int = 1
    shake_max: int = 3
    time_limit_ms: int = 3_000
    minimum_iterations: int = 160
    seed: int = 20260805
    edit_distance_beta: float = 0.6
    diversity_penalty: float = 1.25

    def __post_init__(self) -> None:
        if self.iterations < 0:
            raise ValueError("iterations は 0 以上にしてください")
        if not 1 <= self.shake_min <= self.shake_max:
            raise ValueError("shake は 1 以上で min <= max にしてください")
        if not 0 <= self.minimum_iterations <= self.iterations:
            raise ValueError("minimum_iterations は iterations 以下にしてください")
        if self.time_limit_ms <= 0:
            raise ValueError("time_limit_ms は正にしてください")


@dataclass(frozen=True, slots=True)
class SolverInput:
    days: tuple[PlanningDay, ...]
    spots: Mapping[str, PlanningSpot]
    travel_times: TravelTimeMatrix
    constraints: tuple[Constraint, ...] = ()
    utilities: Mapping[str, float] = field(default_factory=dict)
    previous: Itinerary | None = None
    initial: Itinerary | None = None
    required_spot_ids: frozenset[str] = frozenset()
    excluded_spot_ids: frozenset[str] = frozenset()
    protected_spot_ids: frozenset[str] = frozenset()
    # 削除保護専用(2026-08-04、ADR-0021 レビュー是正・C-1)。
    # `protected_spot_ids` は既存 ops(move/replace/set_stay/set_time 等)由来の
    # 削除保護と共用のため、`_two_opt`/`_or_opt` の並び替え制限には使わない
    # (使うと「全項目 locked 扱い」と同じ過剰制約になる)。編集ターンの既定
    # (集合固定・allow_refill=False)は ops 適用後の全訪問をここに入れる。
    removal_protected_spot_ids: frozenset[str] = frozenset()
    # 挿入プールの制限(2026-08-04、ADR-0021)。None = 制限なし(既定・従来
    # 挙動)。空集合を渡すと `_greedy_fill` は新規スポットを一切挿入しない
    # (編集ターンの既定「集合固定」。`_insert_requirements` の必須挿入は
    # この制限の対象外 — ops による明示追加は常に反映される)。
    insertion_pool: frozenset[str] | None = None
    stay_overrides: Mapping[str, int] = field(default_factory=dict)
    config: SolverConfig = field(default_factory=SolverConfig)


@dataclass(frozen=True, slots=True)
class SolverResult:
    solutions: tuple[Itinerary, Itinerary, Itinerary]
    scores: tuple[float, float, float]
    iterations_run: tuple[int, int]


def default_utility(_: PlanningSpot) -> float:
    """推薦スコアラが接続されるまで使う、差し替え可能な一様効用。"""

    return 1.0


def solve_itinerary(data: SolverInput, *, alternatives: bool = True) -> SolverResult:
    """A=目的関数最良、B=低重複、C=1件減らしたゆったり版を返す。

    `alternatives=False`(編集ターンの既定「集合固定」。ADR-0021)のときは
    解 A だけを計算し、B(2 回目の ILS)・C(緩和解)の生成を省略して
    同じ解を 3 スロットに詰めて返す。呼び出し側(`ItineraryService`)は
    `solutions[0]` だけを使い、解選択 LLM も呼ばない。
    """

    _validate_solver_input(data)
    base_seed = data.config.seed
    solution_a, score_a, iterations_a = _solve_one(data, random.Random(base_seed))
    if not alternatives:
        return SolverResult(
            solutions=(solution_a, solution_a, solution_a),
            scores=(score_a, score_a, score_a),
            iterations_run=(iterations_a, 0),
        )
    overlap = frozenset(itinerary_spot_ids(solution_a))
    solution_b, score_b, iterations_b = _solve_one(
        data,
        random.Random(base_seed + 1),
        diversity_set=overlap,
    )
    solution_c = _relaxed_solution(solution_a, data)
    score_c = objective_value(solution_c, data)
    return SolverResult(
        solutions=(solution_a, solution_b, solution_c),
        scores=(score_a, score_b, score_c),
        iterations_run=(iterations_a, iterations_b),
    )


def objective_value(
    itinerary: Itinerary,
    data: SolverInput,
    *,
    diversity_set: frozenset[str] = frozenset(),
) -> float:
    utility = sum(_utility(data, spot_id) for spot_id in itinerary_spot_ids(itinerary))
    weighted_penalty, _ = evaluate_constraints(itinerary, data.constraints, data.spots)
    edit_penalty = data.config.edit_distance_beta * editing_distance(itinerary, data.previous)
    overlap_penalty = data.config.diversity_penalty * len(
        set(itinerary_spot_ids(itinerary)) & diversity_set
    )
    return utility - weighted_penalty - edit_penalty - overlap_penalty


def editing_distance(current: Itinerary, previous: Itinerary | None) -> float:
    """集合変更、位置変更、時刻変更を小さな離散距離へ畳み込む。"""

    if previous is None:
        return 0.0
    current_positions = _item_positions(current)
    previous_positions = _item_positions(previous)
    current_ids = set(current_positions)
    previous_ids = set(previous_positions)
    distance = float(len(current_ids ^ previous_ids))
    for spot_id in current_ids & previous_ids:
        current_day, current_position, current_arrive = current_positions[spot_id]
        previous_day, previous_position, previous_arrive = previous_positions[spot_id]
        if (current_day, current_position) != (previous_day, previous_position):
            distance += 1.0
        if current_arrive != previous_arrive:
            distance += 0.25
    return distance


def itinerary_spot_ids(itinerary: Itinerary) -> list[str]:
    return [item.spot_id for day in itinerary.days for item in day.items]


def validate_hard_constraints(
    itinerary: Itinerary,
    spots: Mapping[str, PlanningSpot],
    travel_times: TravelTimeMatrix,
) -> list[str]:
    """spot 実在、重複、時刻、営業時間、閉鎖、移動時間を再検査する。"""

    errors: list[str] = []
    visited: set[str] = set()
    for day_index, day in enumerate(itinerary.days, start=1):
        try:
            visit_date = date.fromisoformat(day.date)
        except ValueError:
            errors.append(f"{day_index}日目の日付が YYYY-MM-DD ではありません")
            continue
        if day.start_min > day.end_min:
            errors.append(f"{day_index}日目の開始が終了より後です")
        for endpoint_name, spot_id in (
            ("origin", day.origin.spot_id),
            ("destination", day.destination.spot_id),
        ):
            if spot_id not in spots:
                errors.append(f"{day_index}日目の{endpoint_name}が実在しません: {spot_id}")
        previous_spot_id = day.origin.spot_id
        previous_depart = day.start_min
        for expected_seq, item in enumerate(day.items, start=1):
            if item.seq != expected_seq:
                errors.append(f"{day_index}日目の seq が連番ではありません")
            if item.spot_id not in spots:
                errors.append(f"実在しない spot_id です: {item.spot_id}")
                continue
            if item.spot_id in visited:
                errors.append(f"spot_id が重複しています: {item.spot_id}")
            visited.add(item.spot_id)
            leg = travel_times.best_leg(previous_spot_id, item.spot_id, log_missing=True)
            selected = next(
                (
                    candidate
                    for candidate in travel_times.modes(previous_spot_id, item.spot_id)
                    if candidate.mode is item.leg_from_prev.mode
                ),
                None,
            )
            if selected is None:
                errors.append(f"到達不能な区間です: {previous_spot_id}->{item.spot_id}")
            elif selected.min != item.leg_from_prev.min:
                errors.append(f"移動時間が行列と一致しません: {previous_spot_id}->{item.spot_id}")
            if item.arrive_min != previous_depart + item.leg_from_prev.min:
                errors.append(f"到着時刻が移動時間と一致しません: {item.spot_id}")
            if item.depart_min != item.arrive_min + item.stay_min:
                errors.append(f"出発時刻が滞在時間と一致しません: {item.spot_id}")
            if not _spot_available(
                spots[item.spot_id],
                visit_date,
                item.arrive_min,
                item.depart_min,
            ):
                errors.append(f"営業時間外または季節閉鎖中です: {item.spot_id}")
            previous_spot_id = item.spot_id
            previous_depart = item.depart_min
            _ = leg  # 最速行の欠損ログも上で一度だけ発火させる
        if day.items:
            return_leg = travel_times.best_leg(
                previous_spot_id, day.destination.spot_id, log_missing=True
            )
            if return_leg is None:
                errors.append(
                    f"到達不能な帰着区間です: {previous_spot_id}->{day.destination.spot_id}"
                )
            elif previous_depart + return_leg.min > day.end_min:
                errors.append(f"{day_index}日目の終了時刻を超えています")
        else:
            direct_leg = travel_times.best_leg(
                day.origin.spot_id,
                day.destination.spot_id,
                log_missing=True,
            )
            if direct_leg is None:
                errors.append(
                    f"到達不能な直行区間です: {day.origin.spot_id}->{day.destination.spot_id}"
                )
            elif day.start_min + direct_leg.min > day.end_min:
                errors.append(f"{day_index}日目の直行移動が終了時刻を超えています")
    return errors


def _solve_one(
    data: SolverInput,
    rng: random.Random,
    *,
    diversity_set: frozenset[str] = frozenset(),
) -> tuple[Itinerary, float, int]:
    routes = _initial_routes(data)
    routes = _make_feasible(routes, data)
    routes = _insert_requirements(routes, data, diversity_set)
    routes = _greedy_fill(routes, data, diversity_set)
    routes = _local_search(routes, data)
    best = _schedule_routes(routes, data)
    if best is None:  # 空旅程は同一起終点なら必ず作れる
        routes = [[] for _ in data.days]
        best = _schedule_routes(routes, data)
    if best is None:
        raise ValueError("物理的に整合する空旅程を構成できません")
    best_score = objective_value(best, data, diversity_set=diversity_set)
    best_travel = _total_travel_minutes(routes, data)
    best_signature = _route_signature(routes)
    best_routes = _copy_routes(routes)
    current_routes = _copy_routes(routes)
    current_score = best_score
    deadline = time.perf_counter() + data.config.time_limit_ms / 1000
    iterations_run = 0
    for iteration in range(data.config.iterations):
        if iteration >= data.config.minimum_iterations and time.perf_counter() >= deadline:
            break
        candidate_routes, removed = _shake(current_routes, data, rng)
        candidate_routes = _greedy_fill(
            candidate_routes,
            data,
            diversity_set,
            temporarily_excluded=removed,
            rng=rng,
        )
        candidate_routes = _local_search(candidate_routes, data)
        candidate = _schedule_routes(candidate_routes, data)
        iterations_run += 1
        if candidate is None:
            continue
        score = objective_value(candidate, data, diversity_set=diversity_set)
        travel = _total_travel_minutes(candidate_routes, data)
        signature = _route_signature(candidate_routes)
        if score > best_score + 1e-9 or (
            abs(score - best_score) <= 1e-9 and (travel, signature) < (best_travel, best_signature)
        ):
            routes = candidate_routes
            best = candidate
            best_score = score
            best_travel = travel
            best_signature = signature
            best_routes = _copy_routes(candidate_routes)
        # 同点面だけは歩いて局所最適から抜ける。悪化時は最良解へ戻す。
        if score >= current_score - 1e-9:
            current_routes = candidate_routes
            current_score = score
        else:
            current_routes = _copy_routes(best_routes)
            current_score = best_score
    best = _with_concessions(best, data)
    return best, best_score, iterations_run


def _initial_routes(data: SolverInput) -> list[list[str]]:
    seed = data.initial or data.previous
    routes = [[] for _ in data.days]
    if seed is None:
        return routes
    seen: set[str] = set()
    for day_index, day in enumerate(seed.days[: len(routes)]):
        for item in day.items:
            if (
                item.spot_id in data.spots
                and item.spot_id not in data.excluded_spot_ids
                and item.spot_id not in seen
            ):
                routes[day_index].append(item.spot_id)
                seen.add(item.spot_id)
    return routes


def _make_feasible(routes: list[list[str]], data: SolverInput) -> list[list[str]]:
    candidate = _copy_routes(routes)
    while not _routes_feasible(candidate, data):
        removable = [
            (day_index, position, spot_id)
            for day_index, route in enumerate(candidate)
            for position, spot_id in enumerate(route)
            if spot_id not in _removal_protected_spot_ids(data)
        ]
        if not removable:
            locked = _locked_spot_ids(data)
            removable = [
                (day_index, position, spot_id)
                for day_index, route in enumerate(candidate)
                for position, spot_id in enumerate(route)
                if spot_id not in locked
            ]
        if not removable:
            return [[] for _ in data.days]
        day_index, position, _ = min(
            removable,
            key=lambda entry: (_utility(data, entry[2]), entry[2], entry[0], entry[1]),
        )
        candidate[day_index].pop(position)
    return candidate


def _insert_requirements(
    routes: list[list[str]],
    data: SolverInput,
    diversity_set: frozenset[str],
) -> list[list[str]]:
    candidate = _copy_routes(routes)
    required_groups: list[set[str]] = [{spot_id} for spot_id in sorted(data.required_spot_ids)]
    for constraint in data.constraints:
        if constraint.pred is PredEnum.REQUIRE:
            required_groups.append(target_spot_ids(str(constraint.args["target"]), data.spots))
    for group in required_groups:
        if any(spot_id in group for route in candidate for spot_id in route):
            continue
        available = sorted(
            group
            - set(_flatten_routes(candidate))
            - set(data.excluded_spot_ids)
            - _endpoint_ids(data)
        )
        insertion = _best_insertion(
            candidate,
            available,
            data,
            diversity_set,
            require_positive_gain=False,
        )
        if insertion is not None:
            day_index, position, spot_id = insertion
            candidate[day_index].insert(position, spot_id)
    return candidate


def _greedy_fill(
    routes: list[list[str]],
    data: SolverInput,
    diversity_set: frozenset[str],
    *,
    temporarily_excluded: frozenset[str] = frozenset(),
    rng: random.Random | None = None,
) -> list[list[str]]:
    candidate = _copy_routes(routes)
    while True:
        available_set = (
            set(data.spots)
            - set(_flatten_routes(candidate))
            - set(data.excluded_spot_ids)
            - set(temporarily_excluded)
            - _endpoint_ids(data)
        )
        if data.insertion_pool is not None:
            # ADR-0021: 編集ターンの既定(集合固定)では挿入プールが空集合に
            # なり、ここで新規スポットの候補が常にゼロになる。
            available_set &= set(data.insertion_pool)
        available = sorted(available_set)
        insertion = _best_insertion(
            candidate,
            available,
            data,
            diversity_set,
            require_positive_gain=True,
            rng=rng,
        )
        if insertion is None:
            return candidate
        day_index, position, spot_id = insertion
        candidate[day_index].insert(position, spot_id)


def _best_insertion(
    routes: list[list[str]],
    spot_ids: Sequence[str],
    data: SolverInput,
    diversity_set: frozenset[str],
    *,
    require_positive_gain: bool,
    rng: random.Random | None = None,
) -> tuple[int, int, str] | None:
    if not _routes_feasible(routes, data):
        return None
    options: list[tuple[tuple[float, float, str, int, int], int, int, str]] = []
    for spot_id in spot_ids:
        if spot_id not in data.spots:
            continue
        gain = _marginal_utility(spot_id, routes, data, diversity_set)
        if require_positive_gain and gain <= 1e-9:
            continue
        for day_index, route in enumerate(routes):
            for position in range(len(route) + 1):
                extra_travel = _insertion_extra_travel(
                    route,
                    data.days[day_index],
                    position,
                    spot_id,
                    data.travel_times,
                )
                if extra_travel is None:
                    continue
                ratio = gain / max(1, extra_travel)
                key = (ratio, gain, _reverse_text(spot_id), -day_index, -position)
                options.append((key, day_index, position, spot_id))
    options.sort(key=lambda value: value[0], reverse=True)
    feasible: list[tuple[int, int, str]] = []
    feasible_spot_ids: set[str] = set()
    for _, day_index, position, spot_id in options:
        if rng is not None and spot_id in feasible_spot_ids:
            continue
        trial = _copy_routes(routes)
        trial[day_index].insert(position, spot_id)
        if _routes_feasible(trial, data):
            feasible.append((day_index, position, spot_id))
            feasible_spot_ids.add(spot_id)
            if rng is None or len(feasible) >= 5:
                break
    if not feasible:
        return None
    return feasible[0] if rng is None else feasible[rng.randrange(len(feasible))]


def _shake(
    routes: list[list[str]], data: SolverInput, rng: random.Random
) -> tuple[list[list[str]], frozenset[str]]:
    candidate = _copy_routes(routes)
    fixed = _removal_protected_spot_ids(data)
    choices: list[tuple[int, int, int]] = []
    for day_index, route in enumerate(candidate):
        for length in range(data.config.shake_min, data.config.shake_max + 1):
            for start in range(0, len(route) - length + 1):
                if all(spot_id not in fixed for spot_id in route[start : start + length]):
                    choices.append((day_index, start, length))
    if not choices:
        return candidate, frozenset()
    day_index, start, length = choices[rng.randrange(len(choices))]
    removed = frozenset(candidate[day_index][start : start + length])
    del candidate[day_index][start : start + length]
    return candidate, removed


def _local_search(routes: list[list[str]], data: SolverInput) -> list[list[str]]:
    candidate = _copy_routes(routes)
    candidate = _two_opt(candidate, data)
    return _or_opt(candidate, data)


def _two_opt(routes: list[list[str]], data: SolverInput) -> list[list[str]]:
    candidate = _copy_routes(routes)
    fixed = _fixed_spot_ids(data)
    improved = True
    while improved:
        improved = False
        baseline = _total_travel_minutes(candidate, data)
        best_trial: list[list[str]] | None = None
        best_key: tuple[int, tuple[tuple[str, ...], ...]] | None = None
        for day_index, route in enumerate(candidate):
            for start in range(len(route) - 1):
                for end in range(start + 2, len(route) + 1):
                    if any(spot_id in fixed for spot_id in route[start:end]):
                        continue
                    trial = _copy_routes(candidate)
                    trial[day_index][start:end] = reversed(trial[day_index][start:end])
                    if not _routes_feasible(trial, data):
                        continue
                    travel = _total_travel_minutes(trial, data)
                    key = (travel, _route_signature(trial))
                    if travel < baseline and (best_key is None or key < best_key):
                        best_key = key
                        best_trial = trial
        if best_trial is not None:
            candidate = best_trial
            improved = True
    return candidate


def _or_opt(routes: list[list[str]], data: SolverInput) -> list[list[str]]:
    candidate = _copy_routes(routes)
    fixed = _fixed_spot_ids(data)
    improved = True
    while improved:
        improved = False
        baseline = _total_travel_minutes(candidate, data)
        best_trial: list[list[str]] | None = None
        best_key: tuple[int, tuple[tuple[str, ...], ...]] | None = None
        for source_day, route in enumerate(candidate):
            for source_position, spot_id in enumerate(route):
                if spot_id in fixed:
                    continue
                for target_day, target_route in enumerate(candidate):
                    for target_position in range(len(target_route) + 1):
                        if source_day == target_day and target_position in {
                            source_position,
                            source_position + 1,
                        }:
                            continue
                        trial = _copy_routes(candidate)
                        moved = trial[source_day].pop(source_position)
                        adjusted_position = target_position
                        if source_day == target_day and target_position > source_position:
                            adjusted_position -= 1
                        trial[target_day].insert(adjusted_position, moved)
                        if not _routes_feasible(trial, data):
                            continue
                        travel = _total_travel_minutes(trial, data)
                        key = (travel, _route_signature(trial))
                        if travel < baseline and (best_key is None or key < best_key):
                            best_key = key
                            best_trial = trial
        if best_trial is not None:
            candidate = best_trial
            improved = True
    return candidate


def _schedule_routes(routes: Sequence[Sequence[str]], data: SolverInput) -> Itinerary | None:
    preferred_mode = _preferred_mode(data.constraints)
    itinerary = _schedule_with_mode(routes, data, preferred_mode)
    if itinerary is None and preferred_mode is not None:
        itinerary = _schedule_with_mode(routes, data, None)
    return itinerary


def _routes_feasible(routes: Sequence[Sequence[str]], data: SolverInput) -> bool:
    preferred_mode = _preferred_mode(data.constraints)
    return _routes_feasible_with_mode(routes, data, preferred_mode) or (
        preferred_mode is not None and _routes_feasible_with_mode(routes, data, None)
    )


def _routes_feasible_with_mode(
    routes: Sequence[Sequence[str]], data: SolverInput, preferred_mode: Mode | None
) -> bool:
    if not _respects_locked_order(routes, data):
        return False
    seen: set[str] = set()
    for day_spec, route in zip(data.days, routes, strict=True):
        try:
            visit_date = date.fromisoformat(day_spec.date)
        except ValueError:
            return False
        if day_spec.start_min > day_spec.end_min:
            return False
        previous = day_spec.origin_spot_id
        depart = day_spec.start_min
        for spot_id in route:
            spot = data.spots.get(spot_id)
            if spot is None or spot_id in seen:
                return False
            leg = data.travel_times.best_leg(
                previous,
                spot_id,
                preferred_mode=preferred_mode,
                log_missing=True,
            )
            if leg is None:
                return False
            arrive = depart + leg.min
            stay = int(data.stay_overrides.get(spot_id, spot.stay_min))
            depart = arrive + stay
            if stay <= 0 or not _spot_available(spot, visit_date, arrive, depart):
                return False
            previous = spot_id
            seen.add(spot_id)
        if route:
            return_leg = data.travel_times.best_leg(
                previous,
                day_spec.destination_spot_id,
                preferred_mode=preferred_mode,
                log_missing=True,
            )
            if return_leg is None or depart + return_leg.min > day_spec.end_min:
                return False
        else:
            direct_leg = data.travel_times.best_leg(
                day_spec.origin_spot_id,
                day_spec.destination_spot_id,
                preferred_mode=preferred_mode,
                log_missing=True,
            )
            if direct_leg is None or day_spec.start_min + direct_leg.min > day_spec.end_min:
                return False
    return True


def _schedule_with_mode(
    routes: Sequence[Sequence[str]], data: SolverInput, preferred_mode: Mode | None
) -> Itinerary | None:
    if not _respects_locked_order(routes, data):
        return None
    lock_map, note_map = _item_attributes(data.initial or data.previous)
    days: list[ItineraryDay] = []
    seen: set[str] = set()
    for day_spec, route in zip(data.days, routes, strict=True):
        try:
            visit_date = date.fromisoformat(day_spec.date)
        except ValueError:
            return None
        if day_spec.start_min > day_spec.end_min:
            return None
        items: list[ItineraryItem] = []
        previous_spot_id = day_spec.origin_spot_id
        previous_depart = day_spec.start_min
        for sequence, spot_id in enumerate(route, start=1):
            spot = data.spots.get(spot_id)
            if spot is None or spot_id in seen:
                return None
            leg = data.travel_times.best_leg(
                previous_spot_id,
                spot_id,
                preferred_mode=preferred_mode,
                log_missing=True,
            )
            if leg is None:
                return None
            arrive = previous_depart + leg.min
            stay = int(data.stay_overrides.get(spot_id, spot.stay_min))
            if stay <= 0:
                return None
            depart = arrive + stay
            if not _spot_available(spot, visit_date, arrive, depart):
                return None
            items.append(
                ItineraryItem(
                    seq=sequence,
                    spot_id=spot_id,
                    arrive_min=arrive,
                    stay_min=stay,
                    depart_min=depart,
                    leg_from_prev=LegFromPrev(mode=leg.mode, min=leg.min, route_id=None),
                    locked=lock_map.get(spot_id, False),
                    note=note_map.get(spot_id),
                )
            )
            previous_spot_id = spot_id
            previous_depart = depart
            seen.add(spot_id)
        if items:
            return_leg = data.travel_times.best_leg(
                previous_spot_id,
                day_spec.destination_spot_id,
                preferred_mode=preferred_mode,
                log_missing=True,
            )
            if return_leg is None or previous_depart + return_leg.min > day_spec.end_min:
                return None
        else:
            direct_leg = data.travel_times.best_leg(
                day_spec.origin_spot_id,
                day_spec.destination_spot_id,
                preferred_mode=preferred_mode,
                log_missing=True,
            )
            if direct_leg is None or day_spec.start_min + direct_leg.min > day_spec.end_min:
                return None
        days.append(
            ItineraryDay(
                date=day_spec.date,
                start_min=day_spec.start_min,
                end_min=day_spec.end_min,
                origin=SpotEndpoint(spot_id=day_spec.origin_spot_id),
                destination=SpotEndpoint(spot_id=day_spec.destination_spot_id),
                items=items,
            )
        )
    return Itinerary(
        days=days,
        concessions=[],
        version=data.previous.version if data.previous else 0,
    )


def _relaxed_solution(solution_a: Itinerary, data: SolverInput) -> Itinerary:
    routes = [[item.spot_id for item in day.items] for day in solution_a.days]
    removable = [
        spot_id
        for route in routes
        for spot_id in route
        if spot_id not in _removal_protected_spot_ids(data)
    ]
    if not removable:
        return _with_concessions(solution_a.model_copy(deep=True), data)
    removed = min(removable, key=lambda spot_id: (_utility(data, spot_id), spot_id))
    before_elapsed = _total_elapsed(solution_a, data)
    reduced = [[spot_id for spot_id in route if spot_id != removed] for route in routes]
    base_overrides = {
        item.spot_id: item.stay_min
        for day in solution_a.days
        for item in day.items
        if item.spot_id != removed
    }
    reduced_data = replace(data, stay_overrides=base_overrides)
    relaxed = _schedule_routes(reduced, reduced_data)
    if relaxed is None:
        return _with_concessions(solution_a.model_copy(deep=True), data)
    freed = max(0, before_elapsed - _total_elapsed(relaxed, reduced_data))
    recipients = [item.spot_id for day in relaxed.days for item in day.items if not item.locked]
    overrides = dict(base_overrides)
    for minute_index in range(freed):
        if not recipients:
            break
        spot_id = recipients[minute_index % len(recipients)]
        trial_overrides = {**overrides, spot_id: overrides[spot_id] + 1}
        trial_data = replace(data, stay_overrides=trial_overrides)
        trial = _schedule_routes(reduced, trial_data)
        if trial is not None:
            overrides = trial_overrides
            relaxed = trial
    return _with_concessions(relaxed, replace(data, stay_overrides=overrides))


def _with_concessions(itinerary: Itinerary, data: SolverInput) -> Itinerary:
    _, concessions = evaluate_constraints(itinerary, data.constraints, data.spots)
    return itinerary.model_copy(update={"concessions": concessions}, deep=True)


def _total_elapsed(itinerary: Itinerary, data: SolverInput) -> int:
    total = 0
    for day in itinerary.days:
        if not day.items:
            continue
        last = day.items[-1]
        return_leg = data.travel_times.best_leg(last.spot_id, day.destination.spot_id)
        if return_leg is not None:
            total += last.depart_min + return_leg.min - day.start_min
    return total


def _total_travel_minutes(routes: Sequence[Sequence[str]], data: SolverInput) -> int:
    total = 0
    for day, route in zip(data.days, routes, strict=True):
        previous = day.origin_spot_id
        for spot_id in route:
            leg = data.travel_times.best_leg(previous, spot_id)
            if leg is None:
                return 10**9
            total += leg.min
            previous = spot_id
        if route:
            leg = data.travel_times.best_leg(previous, day.destination_spot_id)
            if leg is None:
                return 10**9
            total += leg.min
    return total


def _spot_available(spot: PlanningSpot, visit_date: date, arrive: int, depart: int) -> bool:
    if visit_date.month in spot.season_closed_months:
        return False
    intervals = opening_intervals(spot.open_hours, visit_date.weekday())
    return intervals is None or any(start <= arrive and depart <= end for start, end in intervals)


def opening_intervals(open_hours: Any, weekday: int) -> tuple[tuple[int, int], ...] | None:
    """現在のシードで使う OSM opening_hours 部分集合を分の区間へ変換する。

    `None` は営業時間概念なし、空 tuple はその曜日が休業を表す。
    """

    if open_hours is None:
        return None
    if isinstance(open_hours, str):
        return _parse_opening_hours(open_hours.strip(), weekday)
    if isinstance(open_hours, Mapping):
        value = open_hours.get(str(weekday)) or open_hours.get(_WEEKDAY_KEYS[weekday])
        if value is None:
            return ()
        if isinstance(value, Sequence) and not isinstance(value, str):
            intervals: list[tuple[int, int]] = []
            for entry in value:
                if isinstance(entry, Sequence) and len(entry) == 2:
                    intervals.append((int(entry[0]), int(entry[1])))
            return tuple(intervals)
    logger.warning(
        "itinerary_open_hours_unparsed",
        extra={"degraded": True, "open_hours": open_hours},
    )
    return None


_WEEKDAY_KEYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_DAY_INDEX = {value: index for index, value in enumerate(_WEEKDAY_KEYS)}
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


@lru_cache(maxsize=256)
def _parse_opening_hours(value: str, weekday: int) -> tuple[tuple[int, int], ...] | None:
    if value == "24/7":
        return None
    intervals: list[tuple[int, int]] = []
    parsed_any = False
    for rule in value.split(";"):
        parts = rule.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        day_expression, time_expression = parts
        days = _expand_days(day_expression)
        if days is None:
            continue
        parsed_any = True
        if weekday not in days:
            continue
        for time_range in time_expression.split(","):
            match = _TIME_RANGE.match(time_range.strip())
            if match is None:
                continue
            start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
            start = start_hour * 60 + start_minute
            end = end_hour * 60 + end_minute
            if end < start:
                end += 24 * 60
            intervals.append((start, end))
    if not parsed_any:
        logger.warning(
            "itinerary_open_hours_unparsed",
            extra={"degraded": True, "open_hours": value},
        )
        return None
    return tuple(sorted(intervals))


def _expand_days(expression: str) -> set[int] | None:
    result: set[int] = set()
    try:
        for part in expression.split(","):
            if "-" not in part:
                result.add(_DAY_INDEX[part])
                continue
            start_text, end_text = part.split("-", maxsplit=1)
            start = _DAY_INDEX[start_text]
            end = _DAY_INDEX[end_text]
            index = start
            while True:
                result.add(index)
                if index == end:
                    break
                index = (index + 1) % 7
    except (KeyError, ValueError):
        return None
    return result


def _preferred_mode(constraints: Sequence[Constraint]) -> Mode | None:
    choices = [
        (constraint.weight, Mode(str(constraint.args["mode"])))
        for constraint in constraints
        if constraint.pred is PredEnum.MODE_PREF
    ]
    return max(choices, default=(0.0, None), key=lambda value: (value[0], value[1] or Mode.CAR))[1]


def _utility(data: SolverInput, spot_id: str) -> float:
    value = data.utilities.get(spot_id)
    return float(value) if value is not None else default_utility(data.spots[spot_id])


def _marginal_utility(
    spot_id: str,
    routes: Sequence[Sequence[str]],
    data: SolverInput,
    diversity_set: frozenset[str],
) -> float:
    """貪欲挿入用の効用。位置依存述語は ILS 後の完全評価へ任せる。"""

    gain = _utility(data, spot_id)
    current_ids = set(_flatten_routes(routes))
    if data.previous is not None and spot_id not in set(itinerary_spot_ids(data.previous)):
        gain -= data.config.edit_distance_beta
    if spot_id in diversity_set:
        gain -= data.config.diversity_penalty
    for constraint in data.constraints:
        target = constraint.args.get("target")
        if not isinstance(target, str) or spot_id not in target_spot_ids(target, data.spots):
            continue
        if constraint.pred is PredEnum.WEIGHT:
            gain += constraint.weight * float(constraint.args["w"])
        elif constraint.pred is PredEnum.EXCLUDE:
            gain -= constraint.weight
        elif constraint.pred is PredEnum.COUNT_AT_MOST:
            matching_count = len(current_ids & target_spot_ids(target, data.spots))
            if matching_count >= int(constraint.args["n"]):
                gain -= constraint.weight
        elif constraint.pred is PredEnum.COUNT_AT_LEAST:
            matching_count = len(current_ids & target_spot_ids(target, data.spots))
            if matching_count < int(constraint.args["n"]):
                gain += constraint.weight
    return gain


def _insertion_extra_travel(
    route: Sequence[str],
    day: PlanningDay,
    position: int,
    spot_id: str,
    travel_times: TravelTimeMatrix,
) -> int | None:
    previous = day.origin_spot_id if position == 0 else route[position - 1]
    following = day.destination_spot_id if position == len(route) else route[position]
    inbound = travel_times.best_leg(previous, spot_id, log_missing=True)
    outbound = travel_times.best_leg(spot_id, following, log_missing=True)
    direct = travel_times.best_leg(previous, following, log_missing=True)
    if inbound is None or outbound is None or direct is None:
        return None
    return inbound.min + outbound.min - direct.min


def _validate_solver_input(data: SolverInput) -> None:
    if not data.days:
        raise ValueError("days は 1 日以上必要です")
    for day in data.days:
        if day.origin_spot_id not in data.spots:
            raise ValueError(f"起点の spot_id が実在しません: {day.origin_spot_id}")
        if day.destination_spot_id not in data.spots:
            raise ValueError(f"終点の spot_id が実在しません: {day.destination_spot_id}")
        date.fromisoformat(day.date)
    unknown = (set(data.required_spot_ids) | set(data.excluded_spot_ids)) - set(data.spots)
    if unknown:
        raise ValueError(f"実在しない spot_id です: {sorted(unknown)}")


def _fixed_spot_ids(data: SolverInput) -> set[str]:
    """並び替え(2-opt / or-opt)を止める集合。

    2026-08-04 レビュー是正(Critical・C-1): `locked` だけを見る。
    `protected_spot_ids`/`removal_protected_spot_ids` は削除保護専用であり、
    並び替えまで止めると ADR-0021 が明示的に却下した「全項目 locked 扱い」と
    同じ挙動になる(採らなかった案)。並びの再調整は `locked` の相対順序
    保持だけで表現する。
    """

    return _locked_spot_ids(data)


def _removal_protected_spot_ids(data: SolverInput) -> set[str]:
    """削除(shake の除去・実行可能化の第一段・緩和解の間引き)から保護する集合。

    2026-08-04 レビュー是正(Critical・C-1): `locked`(相対順序も固定)に加え、
    `protected_spot_ids`(ops が触れた項目の削除保護。既存の ops.py 由来)と
    `removal_protected_spot_ids`(ADR-0021 の集合固定編集で ops 適用後の
    全訪問を保護)を合わせる。並び替え(`_fixed_spot_ids`)とは別物。
    """

    return (
        _locked_spot_ids(data)
        | set(data.protected_spot_ids)
        | set(data.removal_protected_spot_ids)
    )


def _locked_spot_ids(data: SolverInput) -> set[str]:
    baseline = data.initial or data.previous
    if baseline is None:
        return set()
    return {item.spot_id for day in baseline.days for item in day.items if item.locked}


def _respects_locked_order(routes: Sequence[Sequence[str]], data: SolverInput) -> bool:
    baseline = data.initial or data.previous
    if baseline is None:
        return True
    candidate_positions = {
        spot_id: (day_index, position)
        for day_index, route in enumerate(routes)
        for position, spot_id in enumerate(route)
    }
    for baseline_day_index, day in enumerate(baseline.days):
        baseline_ids = [item.spot_id for item in day.items]
        for baseline_position, item in enumerate(day.items):
            if not item.locked or item.spot_id not in candidate_positions:
                continue
            candidate_day, candidate_position = candidate_positions[item.spot_id]
            if candidate_day != baseline_day_index:
                return False
            before = set(baseline_ids[:baseline_position]) & set(candidate_positions)
            after = set(baseline_ids[baseline_position + 1 :]) & set(candidate_positions)
            if any(
                candidate_positions[spot_id][0] != candidate_day
                or candidate_positions[spot_id][1] >= candidate_position
                for spot_id in before
            ):
                return False
            if any(
                candidate_positions[spot_id][0] != candidate_day
                or candidate_positions[spot_id][1] <= candidate_position
                for spot_id in after
            ):
                return False
    return True


def _endpoint_ids(data: SolverInput) -> set[str]:
    return {
        spot_id for day in data.days for spot_id in (day.origin_spot_id, day.destination_spot_id)
    }


def _item_attributes(itinerary: Itinerary | None) -> tuple[dict[str, bool], dict[str, str | None]]:
    if itinerary is None:
        return {}, {}
    return (
        {item.spot_id: item.locked for day in itinerary.days for item in day.items},
        {item.spot_id: item.note for day in itinerary.days for item in day.items},
    )


def _item_positions(itinerary: Itinerary) -> dict[str, tuple[int, int, int]]:
    return {
        item.spot_id: (day_index, position, item.arrive_min)
        for day_index, day in enumerate(itinerary.days)
        for position, item in enumerate(day.items)
    }


def _copy_routes(routes: Sequence[Sequence[str]]) -> list[list[str]]:
    return [list(route) for route in routes]


def _flatten_routes(routes: Sequence[Sequence[str]]) -> Iterable[str]:
    return (spot_id for route in routes for spot_id in route)


def _route_signature(routes: Sequence[Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(route) for route in routes)


def _reverse_text(value: str) -> str:
    """max key の中でも通常の文字列昇順を優先するための安定キー。"""

    return "".join(chr(0x10FFFF - ord(character)) for character in value)
