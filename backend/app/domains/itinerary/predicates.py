"""制約 DSL 17 述語の検証とペナルティレジストリ。"""

from __future__ import annotations

import contextvars
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

from pydantic import ValidationError

from app.domains.itinerary.types import (
    Concession,
    Constraint,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    Mode,
    PredEnum,
    UnmodeledConstraint,
    parse_minute,
)


class PredicateSpot(Protocol):
    spot_id: str
    tags_ja: Sequence[str]


SpotContext: TypeAlias = Mapping[str, PredicateSpot | Mapping[str, Any]]
PenaltyFunction: TypeAlias = Callable[[Itinerary, Mapping[str, Any]], tuple[float, str]]

_SPOTS: contextvars.ContextVar[SpotContext | None] = contextvars.ContextVar(
    "itinerary_predicate_spots", default=None
)


# 分単位の述語は小さく、必須・除外は明確に強くする。明示 weight があれば上書きする。
DEFAULT_WEIGHTS: dict[PredEnum, float] = {
    PredEnum.WEIGHT: 1.0,
    PredEnum.REQUIRE: 12.0,
    PredEnum.EXCLUDE: 12.0,
    PredEnum.COUNT_AT_MOST: 2.0,
    PredEnum.COUNT_AT_LEAST: 2.0,
    PredEnum.FIRST: 2.0,
    PredEnum.LAST: 2.0,
    PredEnum.BEFORE: 2.0,
    PredEnum.NOT_CONSECUTIVE: 1.5,
    PredEnum.SAME_DAY: 2.0,
    PredEnum.DIFFERENT_DAY: 2.0,
    PredEnum.TIME_WINDOW: 0.03,
    PredEnum.STAY_AT_LEAST: 0.03,
    PredEnum.DAY_PART_LOAD: 1.5,
    PredEnum.MAX_LEG_MIN: 0.025,
    PredEnum.MODE_PREF: 0.75,
    PredEnum.LUNCH_BREAK: 4.0,
}

_PART_BOUNDS = {
    "morning": (0, 12 * 60),
    "午前": (0, 12 * 60),
    "afternoon": (12 * 60, 17 * 60),
    "午後": (12 * 60, 17 * 60),
    "evening": (17 * 60, 24 * 60),
    "夕方": (17 * 60, 24 * 60),
    "night": (17 * 60, math.inf),
    "夜": (17 * 60, math.inf),
}
_PART_LIMITS = {
    "low": 2,
    "ゆっくり": 2,
    "medium": 3,
    "normal": 3,
    "普通": 3,
    "high": 4,
    "packed": 4,
    "多め": 4,
}


@contextmanager
def predicate_context(spots: SpotContext) -> Iterable[None]:
    """2 引数というレジストリ契約を保ったまま、タグ照合用の静的情報を渡す。"""

    token = _SPOTS.set(spots)
    try:
        yield
    finally:
        _SPOTS.reset(token)


def penalty_weight(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    """一致した訪問の効用加算を、負のペナルティとして目的関数へ渡す。"""

    count = len(_target_items(itinerary, _target(args)))
    bonus = float(args["w"]) * count
    return -bonus, f"{args['target']}の希望を訪問効用へ反映しました"


def penalty_require(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    violation = float(not _target_items(itinerary, _target(args)))
    return violation, f"必須希望の「{args['target']}」を旅程に入れられませんでした"


def penalty_exclude(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    violation = float(len(_target_items(itinerary, _target(args))))
    return violation, f"除外希望の「{args['target']}」が旅程に含まれています"


def penalty_count_at_most(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    count = len(_target_items(itinerary, _target(args)))
    violation = float(max(0, count - int(args["n"])))
    return violation, f"{args['target']}の訪問が希望上限を{int(violation)}件超えています"


def penalty_count_at_least(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    count = len(_target_items(itinerary, _target(args)))
    violation = float(max(0, int(args["n"]) - count))
    return violation, f"{args['target']}の訪問が希望数より{int(violation)}件不足しています"


def penalty_first(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    items = _all_items(itinerary)
    violation = float(not items or not _matches(items[0][2].spot_id, _target(args)))
    return violation, f"{args['target']}を旅程の最初にできませんでした"


def penalty_last(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    items = _all_items(itinerary)
    violation = float(not items or not _matches(items[-1][2].spot_id, _target(args)))
    return violation, f"{args['target']}を旅程の最後にできませんでした"


def penalty_before(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    positions_a = _target_positions(itinerary, str(args["a"]))
    positions_b = _target_positions(itinerary, str(args["b"]))
    if not positions_a or not positions_b:
        violation = 1.0
    else:
        violation = float(min(positions_a) >= max(positions_b))
    return violation, f"{args['a']}を{args['b']}より前に配置できませんでした"


def penalty_not_consecutive(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    target = _target(args)
    pairs = 0
    for day in itinerary.days:
        pairs += sum(
            _matches(left.spot_id, target) and _matches(right.spot_id, target)
            for left, right in zip(day.items, day.items[1:], strict=False)
        )
    return float(pairs), f"{args['target']}に当たる場所が{pairs}組連続しています"


def penalty_same_day(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    days_a = _target_days(itinerary, str(args["a"]))
    days_b = _target_days(itinerary, str(args["b"]))
    if not days_a or not days_b:
        violation = 0.0
    else:
        violation = float(days_a.isdisjoint(days_b))
    return violation, f"{args['a']}と{args['b']}を同じ日にできませんでした"


def penalty_different_day(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    days_a = _target_days(itinerary, str(args["a"]))
    days_b = _target_days(itinerary, str(args["b"]))
    violation = float(len(days_a & days_b))
    return violation, f"{args['a']}と{args['b']}が同じ日に{int(violation)}組あります"


def penalty_time_window(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    start = int(args["from"])
    end = int(args["to"])
    violation = 0
    for _, _, item in _target_items(itinerary, _target(args)):
        violation += max(0, start - item.arrive_min)
        violation += max(0, item.depart_min - end)
    return float(violation), f"{args['target']}の希望時間帯から合計{violation}分外れています"


def penalty_stay_at_least(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    minimum = int(args["min"])
    violation = sum(
        max(0, minimum - item.stay_min) for _, _, item in _target_items(itinerary, _target(args))
    )
    return float(violation), f"{args['target']}の滞在時間が合計{violation}分不足しています"


def penalty_day_part_load(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    day_indexes = _selected_day_indexes(itinerary, args["day"])
    lower, upper = _PART_BOUNDS[str(args["part"])]
    limit = _PART_LIMITS[str(args["level"])]
    count = sum(
        lower <= item.arrive_min < upper
        for day_index in day_indexes
        for item in itinerary.days[day_index].items
    )
    violation = max(0, count - limit * len(day_indexes))
    return float(violation), f"指定時間帯の訪問が希望密度を{violation}件超えています"


def penalty_max_leg_min(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    maximum = int(args["n"])
    violation = sum(
        max(0, item.leg_from_prev.min - maximum) for day in itinerary.days for item in day.items
    )
    return float(violation), f"1回の移動時間が希望上限を合計{violation}分超えています"


def penalty_mode_pref(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    preferred = str(args["mode"])
    violation = sum(
        item.leg_from_prev.mode.value != preferred for day in itinerary.days for item in day.items
    )
    return float(violation), f"希望する移動手段を使わない区間が{violation}件あります"


def penalty_lunch_break(itinerary: Itinerary, args: Mapping[str, Any]) -> tuple[float, str]:
    window_start = int(args["from"])
    window_end = int(args["to"])
    required = int(args["min"])
    violating_days = sum(
        not _has_free_interval(day, window_start, window_end, required) for day in itinerary.days
    )
    return float(violating_days), f"希望時間帯に昼休憩を取れない日が{violating_days}日あります"


PENALTIES: dict[PredEnum, PenaltyFunction] = {
    PredEnum.WEIGHT: penalty_weight,
    PredEnum.REQUIRE: penalty_require,
    PredEnum.EXCLUDE: penalty_exclude,
    PredEnum.COUNT_AT_MOST: penalty_count_at_most,
    PredEnum.COUNT_AT_LEAST: penalty_count_at_least,
    PredEnum.FIRST: penalty_first,
    PredEnum.LAST: penalty_last,
    PredEnum.BEFORE: penalty_before,
    PredEnum.NOT_CONSECUTIVE: penalty_not_consecutive,
    PredEnum.SAME_DAY: penalty_same_day,
    PredEnum.DIFFERENT_DAY: penalty_different_day,
    PredEnum.TIME_WINDOW: penalty_time_window,
    PredEnum.STAY_AT_LEAST: penalty_stay_at_least,
    PredEnum.DAY_PART_LOAD: penalty_day_part_load,
    PredEnum.MAX_LEG_MIN: penalty_max_leg_min,
    PredEnum.MODE_PREF: penalty_mode_pref,
    PredEnum.LUNCH_BREAK: penalty_lunch_break,
}


@dataclass(frozen=True, slots=True)
class ConstraintValidation:
    constraints: list[Constraint]
    unmodeled: list[UnmodeledConstraint]


def normalize_constraints(
    values: Sequence[Constraint | Mapping[str, Any]],
    spots: SpotContext,
    *,
    created_at_version: int,
    used_ids: Iterable[str] = (),
) -> ConstraintValidation:
    """構文・参照を検査し、未知または実在しない入力を `unmodeled` へ分ける。"""

    constraints: list[Constraint] = []
    unmodeled: list[UnmodeledConstraint] = []
    allocated = set(used_ids)
    next_number = _next_constraint_number(allocated)
    for value in values:
        raw = value.model_dump(mode="python") if isinstance(value, Constraint) else dict(value)
        pred_text = str(raw.get("pred", ""))
        args_raw = raw.get("args")
        args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
        source_text = str(raw.get("source_text", ""))
        try:
            pred = PredEnum(pred_text)
        except ValueError:
            unmodeled.append(
                UnmodeledConstraint(
                    pred=pred_text,
                    args=args,
                    source_text=source_text,
                    reason=f"未知の述語です: {pred_text or '(空)'}",
                )
            )
            continue
        try:
            normalized_args = _normalize_args(pred, args, spots)
            constraint_id = str(raw.get("id") or "")
            if not constraint_id:
                while f"c_{next_number:03d}" in allocated:
                    next_number += 1
                constraint_id = f"c_{next_number:03d}"
                next_number += 1
            if constraint_id in allocated:
                raise ValueError(f"制約 id が重複しています: {constraint_id}")
            weight_raw = raw.get("weight", DEFAULT_WEIGHTS[pred])
            constraint = Constraint(
                id=constraint_id,
                pred=pred,
                args=normalized_args,
                weight=float(weight_raw),
                source_message_id=raw.get("source_message_id"),
                source_text=source_text,
                created_at_version=int(raw.get("created_at_version") or created_at_version),
                handling="dsl",
            )
        except (TypeError, ValueError, ValidationError) as exc:
            unmodeled.append(
                UnmodeledConstraint(
                    pred=pred.value,
                    args=args,
                    source_text=source_text,
                    reason=str(exc),
                )
            )
            continue
        allocated.add(constraint.id)
        constraints.append(constraint)
    return ConstraintValidation(constraints=constraints, unmodeled=unmodeled)


def evaluate_constraints(
    itinerary: Itinerary,
    constraints: Sequence[Constraint],
    spots: SpotContext,
) -> tuple[float, list[Concession]]:
    """重み付きペナルティ合計と、正の違反だけの譲歩内訳を返す。"""

    total = 0.0
    concessions: list[Concession] = []
    with predicate_context(spots):
        for constraint in constraints:
            violation, message = PENALTIES[constraint.pred](itinerary, constraint.args)
            total += constraint.weight * violation
            if violation > 0:
                concessions.append(
                    Concession(
                        constraint_id=constraint.id,
                        pred=constraint.pred,
                        args=dict(constraint.args),
                        violation=violation,
                        message_ja=message,
                    )
                )
    return total, concessions


def target_spot_ids(target: str, spots: SpotContext) -> set[str]:
    """spot_id と生タグを同じ target 引数から解決する。"""

    if target in spots:
        return {target}
    return {spot_id for spot_id in spots if target in _tags_for_spot(spot_id, spots)}


def _normalize_args(pred: PredEnum, args: dict[str, Any], spots: SpotContext) -> dict[str, Any]:
    normalized = dict(args)
    if pred in {
        PredEnum.WEIGHT,
        PredEnum.REQUIRE,
        PredEnum.EXCLUDE,
        PredEnum.COUNT_AT_MOST,
        PredEnum.COUNT_AT_LEAST,
        PredEnum.FIRST,
        PredEnum.LAST,
        PredEnum.NOT_CONSECUTIVE,
        PredEnum.TIME_WINDOW,
        PredEnum.STAY_AT_LEAST,
    }:
        _validate_target(normalized.get("target"), spots)
    if pred in {PredEnum.BEFORE, PredEnum.SAME_DAY, PredEnum.DIFFERENT_DAY}:
        _validate_target(normalized.get("a"), spots)
        _validate_target(normalized.get("b"), spots)
    if pred is PredEnum.WEIGHT:
        normalized["w"] = _finite_number(normalized.get("w"), "w")
    elif pred in {PredEnum.COUNT_AT_MOST, PredEnum.COUNT_AT_LEAST}:
        normalized["n"] = _integer(normalized.get("n"), "n", minimum=0)
    elif pred is PredEnum.TIME_WINDOW:
        normalized["from"] = parse_minute(normalized.get("from"), field_name="from")
        normalized["to"] = parse_minute(normalized.get("to"), field_name="to")
        if normalized["from"] > normalized["to"]:
            raise ValueError("time_window.from は to 以下にしてください")
    elif pred is PredEnum.STAY_AT_LEAST:
        normalized["min"] = _integer(normalized.get("min"), "min", minimum=1)
    elif pred is PredEnum.DAY_PART_LOAD:
        day = normalized.get("day")
        if not isinstance(day, (int, str)) or isinstance(day, bool):
            raise ValueError("day は 1 起算の日番号または日付にしてください")
        if isinstance(day, int) and day < 1:
            raise ValueError("day は 1 以上にしてください")
        part = str(normalized.get("part", ""))
        level = str(normalized.get("level", ""))
        if part not in _PART_BOUNDS:
            raise ValueError(f"未定義の時間帯です: {part}")
        if level not in _PART_LIMITS:
            raise ValueError(f"未定義の密度です: {level}")
        normalized["part"] = part
        normalized["level"] = level
    elif pred is PredEnum.MAX_LEG_MIN:
        normalized["n"] = _integer(normalized.get("n"), "n", minimum=0)
    elif pred is PredEnum.MODE_PREF:
        try:
            normalized["mode"] = Mode(str(normalized.get("mode"))).value
        except ValueError as exc:
            raise ValueError("mode は car または foot にしてください") from exc
    elif pred is PredEnum.LUNCH_BREAK:
        normalized["from"] = parse_minute(normalized.get("from"), field_name="from")
        normalized["to"] = parse_minute(normalized.get("to"), field_name="to")
        normalized["min"] = _integer(normalized.get("min"), "min", minimum=1)
        if normalized["from"] > normalized["to"]:
            raise ValueError("lunch_break.from は to 以下にしてください")
    return normalized


def _validate_target(value: Any, spots: SpotContext) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("target は空でない spot_id または生タグにしてください")
    if not target_spot_ids(value, spots):
        raise ValueError(f"実在しない spot_id または生タグです: {value}")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} は数値にしてください")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} は有限値にしてください")
    return result


def _integer(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} は {minimum} 以上の整数にしてください")
    return value


def _next_constraint_number(ids: Iterable[str]) -> int:
    numbers = [int(value[2:]) for value in ids if value.startswith("c_") and value[2:].isdigit()]
    return max(numbers, default=0) + 1


def _target(args: Mapping[str, Any]) -> str:
    return str(args["target"])


def _matches(spot_id: str, target: str) -> bool:
    if spot_id == target:
        return True
    return target in _tags_for_spot(spot_id, _SPOTS.get() or {})


def _tags_for_spot(spot_id: str, spots: SpotContext) -> Sequence[str]:
    spot = spots.get(spot_id)
    if spot is None:
        return ()
    if isinstance(spot, Mapping):
        tags = spot.get("tags_ja", ())
    else:
        tags = spot.tags_ja
    return tags if isinstance(tags, Sequence) and not isinstance(tags, str) else ()


def _all_items(itinerary: Itinerary) -> list[tuple[int, int, ItineraryItem]]:
    return [
        (day_index, item_index, item)
        for day_index, day in enumerate(itinerary.days)
        for item_index, item in enumerate(day.items)
    ]


def _target_items(itinerary: Itinerary, target: str) -> list[tuple[int, int, ItineraryItem]]:
    return [entry for entry in _all_items(itinerary) if _matches(entry[2].spot_id, target)]


def _target_positions(itinerary: Itinerary, target: str) -> list[int]:
    return [
        index
        for index, entry in enumerate(_all_items(itinerary))
        if _matches(entry[2].spot_id, target)
    ]


def _target_days(itinerary: Itinerary, target: str) -> set[int]:
    return {day_index for day_index, _, _ in _target_items(itinerary, target)}


def _selected_day_indexes(itinerary: Itinerary, day: int | str) -> list[int]:
    if isinstance(day, int):
        return [day - 1] if day <= len(itinerary.days) else []
    return [index for index, value in enumerate(itinerary.days) if value.date == day]


def _has_free_interval(day: ItineraryDay, start: int, end: int, required: int) -> bool:
    if required > end - start:
        return False
    occupied: list[tuple[int, int]] = []
    for item in day.items:
        occupied.append((item.arrive_min - item.leg_from_prev.min, item.depart_min))
    cursor = start
    for occupied_start, occupied_end in sorted(occupied):
        if occupied_end <= start or occupied_start >= end:
            continue
        if max(start, occupied_start) - cursor >= required:
            return True
        cursor = max(cursor, min(end, occupied_end))
    return end - cursor >= required
