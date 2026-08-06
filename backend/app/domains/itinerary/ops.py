"""`edit_itinerary` の 8 種類の編集 op を決定的に適用する。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domains.itinerary.types import (
    AddOp,
    Diff,
    Itinerary,
    ItineraryItem,
    LegFromPrev,
    LockOp,
    MovedItem,
    MoveOp,
    Op,
    Position,
    RemoveOp,
    ReplaceOp,
    RevertOp,
    SetStayOp,
    SetTimeOp,
    parse_minute,
    parse_ops,
)


class OpApplicationError(ValueError):
    """op の参照または前提が現在の旅程に合わない。"""


@dataclass(frozen=True, slots=True)
class AppliedOps:
    itinerary: Itinerary
    required_spot_ids: frozenset[str]
    excluded_spot_ids: frozenset[str]
    protected_spot_ids: frozenset[str]
    stay_overrides: dict[str, int]
    preferred_windows: dict[str, tuple[int, int]]


def find_revert(values: Sequence[Op | Mapping[str, Any]]) -> RevertOp | None:
    """revert が 1 つでもあれば他の op を解析前から破棄する。"""

    for value in values:
        if isinstance(value, RevertOp):
            return value
        if isinstance(value, Mapping) and value.get("op") == "revert":
            return RevertOp.model_validate(value)
    return None


def apply_ops(
    itinerary: Itinerary,
    values: Sequence[Op | dict[str, Any]],
    *,
    known_spot_ids: set[str],
    default_stays: Mapping[str, int],
) -> AppliedOps:
    """revert 以外の op を順番どおりドラフトへ反映する。"""

    if find_revert(values) is not None:
        raise OpApplicationError("revert は版管理層で適用してください")
    operations = parse_ops(list(values))
    draft = itinerary.model_copy(deep=True)
    required: set[str] = set()
    excluded: set[str] = set()
    protected: set[str] = set()
    stay_overrides = {item.spot_id: item.stay_min for day in draft.days for item in day.items}
    preferred_windows: dict[str, tuple[int, int]] = {}
    for operation in operations:
        if isinstance(operation, AddOp):
            targets = _resolved_targets(operation.targets)
            for target in targets:
                _require_known(target, known_spot_ids)
                if _find_item(draft, target) is not None:
                    required.add(target)
                    continue
                day_index, position = _add_position(draft, operation.day, operation.after)
                stay = int(default_stays[target])
                draft.days[day_index].items.insert(
                    position,
                    ItineraryItem(
                        seq=1,
                        spot_id=target,
                        arrive_min=draft.days[day_index].start_min,
                        stay_min=stay,
                        depart_min=draft.days[day_index].start_min + stay,
                        leg_from_prev=LegFromPrev(mode="car", min=0, route_id=None),
                    ),
                )
                stay_overrides[target] = stay
                required.add(target)
                excluded.discard(target)
                protected.add(target)
                _resequence(draft)
        elif isinstance(operation, RemoveOp):
            for target in operation.targets:
                _remove_item(draft, target)
                excluded.add(target)
                required.discard(target)
                protected.discard(target)
                stay_overrides.pop(target, None)
                preferred_windows.pop(target, None)
        elif isinstance(operation, MoveOp):
            _move_item(draft, operation)
            protected.add(operation.target)
        elif isinstance(operation, ReplaceOp):
            _require_known(operation.with_, known_spot_ids)
            _replace_item(draft, operation, default_stays)
            excluded.add(operation.target)
            excluded.discard(operation.with_)
            required.add(operation.with_)
            protected.add(operation.with_)
            stay_overrides.pop(operation.target, None)
            stay_overrides[operation.with_] = int(default_stays[operation.with_])
        elif isinstance(operation, LockOp):
            for target in operation.targets:
                day_index, item_index, item = _require_item(draft, target)
                draft.days[day_index].items[item_index] = item.model_copy(
                    update={"locked": operation.locked}
                )
                if operation.locked:
                    protected.add(target)
        elif isinstance(operation, SetStayOp):
            day_index, item_index, item = _require_item(draft, operation.target)
            updated = item.model_copy(
                update={
                    "stay_min": operation.min,
                    "depart_min": item.arrive_min + operation.min,
                }
            )
            draft.days[day_index].items[item_index] = updated
            stay_overrides[operation.target] = operation.min
            protected.add(operation.target)
        elif isinstance(operation, SetTimeOp):
            _set_time(draft, operation, stay_overrides, preferred_windows)
            protected.add(operation.target)
        else:  # pragma: no cover - discriminated union が防ぐ
            raise OpApplicationError(f"未対応の op です: {operation.op}")
        _resequence(draft)
    return AppliedOps(
        itinerary=draft,
        required_spot_ids=frozenset(required),
        excluded_spot_ids=frozenset(excluded),
        protected_spot_ids=frozenset(protected),
        stay_overrides=stay_overrides,
        preferred_windows=preferred_windows,
    )


def calculate_diff(previous: Itinerary, current: Itinerary) -> Diff:
    previous_items = _position_map(previous)
    current_items = _position_map(current)
    previous_ids = set(previous_items)
    current_ids = set(current_items)
    common = previous_ids & current_ids
    moved = [
        MovedItem(
            spot_id=spot_id,
            from_=Position(
                day=previous_items[spot_id][0],
                position=previous_items[spot_id][1],
            ),
            to=Position(day=current_items[spot_id][0], position=current_items[spot_id][1]),
        )
        for spot_id in sorted(common)
        if previous_items[spot_id][:2] != current_items[spot_id][:2]
    ]
    retimed = [
        spot_id
        for spot_id in sorted(common)
        if previous_items[spot_id][2:] != current_items[spot_id][2:]
    ]
    return Diff(
        added=sorted(current_ids - previous_ids),
        removed=sorted(previous_ids - current_ids),
        moved=moved,
        retimed=retimed,
    )


def _resolved_targets(value: list[str] | str) -> list[str]:
    if isinstance(value, str):
        if value.startswith("$"):
            raise OpApplicationError(f"参照が解決されていません: {value}")
        return [value]
    return value


def _require_known(spot_id: str, known_spot_ids: set[str]) -> None:
    if spot_id not in known_spot_ids:
        raise OpApplicationError(f"実在しない spot_id です: {spot_id}")


def _find_item(itinerary: Itinerary, spot_id: str) -> tuple[int, int, ItineraryItem] | None:
    for day_index, day in enumerate(itinerary.days):
        for item_index, item in enumerate(day.items):
            if item.spot_id == spot_id:
                return day_index, item_index, item
    return None


def _require_item(itinerary: Itinerary, spot_id: str) -> tuple[int, int, ItineraryItem]:
    found = _find_item(itinerary, spot_id)
    if found is None:
        raise OpApplicationError(f"旅程に含まれない spot_id です: {spot_id}")
    return found


def _remove_item(itinerary: Itinerary, spot_id: str) -> ItineraryItem:
    day_index, item_index, item = _require_item(itinerary, spot_id)
    itinerary.days[day_index].items.pop(item_index)
    _resequence(itinerary)
    return item


def _add_position(
    itinerary: Itinerary, day_number: int | None, after: str | None
) -> tuple[int, int]:
    if not itinerary.days:
        raise OpApplicationError("旅程に日がありません")
    if after is not None:
        after_day, after_position, _ = _require_item(itinerary, after)
        if day_number is not None and after_day != day_number - 1:
            raise OpApplicationError("day と after が別の日を指しています")
        return after_day, after_position + 1
    day_index = (day_number or 1) - 1
    if not 0 <= day_index < len(itinerary.days):
        raise OpApplicationError(f"day が旅程の範囲外です: {day_number}")
    return day_index, len(itinerary.days[day_index].items)


def _move_item(itinerary: Itinerary, operation: MoveOp) -> None:
    source_day, _, item = _require_item(itinerary, operation.target)
    _remove_item(itinerary, operation.target)
    target_day = operation.day - 1 if operation.day is not None else source_day
    if not 0 <= target_day < len(itinerary.days):
        raise OpApplicationError(f"day が旅程の範囲外です: {operation.day}")
    position = operation.position or len(itinerary.days[target_day].items) + 1
    if position > len(itinerary.days[target_day].items) + 1:
        raise OpApplicationError(f"position が旅程の範囲外です: {position}")
    itinerary.days[target_day].items.insert(position - 1, item)
    _resequence(itinerary)


def _replace_item(
    itinerary: Itinerary,
    operation: ReplaceOp,
    default_stays: Mapping[str, int],
) -> None:
    if operation.with_.startswith("$"):
        raise OpApplicationError(f"参照が解決されていません: {operation.with_}")
    existing = _find_item(itinerary, operation.with_)
    if existing is not None and operation.with_ != operation.target:
        raise OpApplicationError(f"置換先は既に旅程にあります: {operation.with_}")
    day_index, item_index, item = _require_item(itinerary, operation.target)
    stay = int(default_stays[operation.with_])
    itinerary.days[day_index].items[item_index] = item.model_copy(
        update={
            "spot_id": operation.with_,
            "stay_min": stay,
            "depart_min": item.arrive_min + stay,
        }
    )


def _set_time(
    itinerary: Itinerary,
    operation: SetTimeOp,
    stay_overrides: dict[str, int],
    preferred_windows: dict[str, tuple[int, int]],
) -> None:
    if operation.arrive is None and operation.depart is None:
        raise OpApplicationError("set_time は arrive または depart が必要です")
    day_index, item_index, item = _require_item(itinerary, operation.target)
    arrive = (
        parse_minute(operation.arrive, field_name="arrive")
        if operation.arrive is not None
        else item.arrive_min
    )
    depart = (
        parse_minute(operation.depart, field_name="depart")
        if operation.depart is not None
        else arrive + item.stay_min
    )
    if operation.arrive is None:
        arrive = depart - item.stay_min
    if arrive < 0 or depart <= arrive:
        raise OpApplicationError("set_time は depart > arrive >= 0 にしてください")
    stay = depart - arrive
    itinerary.days[day_index].items[item_index] = item.model_copy(
        update={"arrive_min": arrive, "stay_min": stay, "depart_min": depart}
    )
    stay_overrides[operation.target] = stay
    preferred_windows[operation.target] = (arrive, depart)


def _resequence(itinerary: Itinerary) -> None:
    for day in itinerary.days:
        day.items = [
            item.model_copy(update={"seq": index}) for index, item in enumerate(day.items, 1)
        ]


def _position_map(itinerary: Itinerary) -> dict[str, tuple[int, int, int, int, int]]:
    return {
        item.spot_id: (
            day_index,
            item_index,
            item.arrive_min,
            item.depart_min,
            item.stay_min,
        )
        for day_index, day in enumerate(itinerary.days, 1)
        for item_index, item in enumerate(day.items, 1)
    }
