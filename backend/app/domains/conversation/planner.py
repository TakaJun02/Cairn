"""N3 `validate_plan`: P1〜P8、事前条件、参照、専門呼び出し配分。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.domains.conversation.events import EventSinkLike, emit, state_event
from app.domains.conversation.guards import (
    normalize_revert_ops,
    parse_step_reference,
    validate_ask_user,
)
from app.domains.conversation.state import RejectedStep, TurnState
from app.domains.conversation.types import (
    AskUserArgs,
    EditItineraryArgs,
    Intent,
    PlanItineraryArgs,
    PlanStep,
    RecommendArgs,
    ReferenceResolution,
    SearchKnowledgeArgs,
    ToolName,
)
from app.domains.itinerary.types import parse_minute, parse_ops
from app.domains.recommendation.types import RecommendFilter

_JAPAN_TZ = ZoneInfo("Asia/Tokyo")
_SPECIALIST_TOOLS = {
    ToolName.RECOMMEND,
    ToolName.PLAN_ITINERARY,
    ToolName.EDIT_ITINERARY,
}
_SPOT_ID_OUTPUT_TOOLS = {
    ToolName.RECOMMEND,
    ToolName.PLAN_ITINERARY,
    ToolName.EDIT_ITINERARY,
}
_ITINERARY_OUTPUT_TOOLS = {
    ToolName.PLAN_ITINERARY,
    ToolName.EDIT_ITINERARY,
}
_ITINERARY_WRITE_TOOLS = {
    ToolName.PLAN_ITINERARY,
    ToolName.EDIT_ITINERARY,
}


class ReferenceResolutionError(ValueError):
    """実行時の `$N` が前段結果から値を取り出せない。"""


async def validate_plan(
    state: TurnState,
    *,
    event_sink: EventSinkLike = None,
    now: datetime | None = None,
) -> TurnState:
    """検証順を §16.3 どおり固定し、最後に plan イベントを送る。"""

    rejected = list(state.rejected_steps)
    working = [value.model_copy(deep=True) for value in state.plan]

    # P2 — Tool enum
    working = _filter_steps(working, rejected, _p2_known_tool)

    # P3 — Tool ごとの引数 schema
    normalized: list[PlanStep] = []
    for step in working:
        normalized_step = _normalize_llm_step(state, step)
        try:
            parsed_args = _parse_args(normalized_step)
            if _mixed_revert_ops(normalized_step):
                _reject(
                    rejected,
                    normalized_step,
                    "revert_exclusive",
                    "revert と混在した他の op を破棄しました",
                )
            normalized.append(
                normalized_step.model_copy(update={"args": parsed_args}, deep=True)
            )
        except (ValidationError, ValueError, TypeError) as exc:
            _reject(rejected, step, "P3", f"引数 schema に適合しません: {exc}")
    working = normalized

    # P4 — 前方参照・解決可能性・型・出力 Tool・循環
    working = _p4_filter(working, rejected)

    # §20.2 の事前条件（P5 より前）
    working = _apply_preconditions(state, working, rejected, now=now)

    # P5 — ask_user は生き残った plan の末尾のみ・plan 全体で 1 手まで
    p5_working: list[PlanStep] = []
    ask_seen = False
    for index, step in enumerate(working):
        if step.tool != ToolName.ASK_USER.value:
            p5_working.append(step)
            continue
        if ask_seen:
            _reject(rejected, step, "P5", "ask_user は plan 全体で 1 手までです")
            continue
        ask_seen = True
        if index != len(working) - 1:
            _reject(rejected, step, "P5", "ask_user は plan の末尾にしか置けません")
            continue
        p5_working.append(step)
    working = p5_working

    # P6 — 旅程書換えは 1 手
    itinerary_write_seen = False
    p6_working: list[PlanStep] = []
    for step in working:
        if ToolName(step.tool) in _ITINERARY_WRITE_TOOLS:
            if itinerary_write_seen:
                _reject(
                    rejected,
                    step,
                    "P6",
                    "同一ターンで旅程を書き換える手は 1 つまでです",
                )
                continue
            itinerary_write_seen = True
        p6_working.append(step)
    working = p6_working

    # P1 — 無効な手を全部落とした後で初めて 3 手へ切る。
    for step in working[3:]:
        _reject(rejected, step, "P1", "plan の 4 手目以降を破棄しました")
    working = working[:3]

    # P7 — search_knowledge を数えず、実行前に 1 手へ割り当てる。
    llm_budget_step = assign_specialist_budget(working)

    # P8 — 空なら Tool なし。イベントは出さず respond へ進む。
    state.accepted_steps = working
    state.rejected_steps = rejected
    state.llm_budget_step = llm_budget_step
    state.log_fields["accepted_tools"] = [value.tool for value in working]
    state.log_fields["rejected_steps"] = [
        value.model_dump(mode="json") for value in rejected
    ]
    if working:
        await emit(
            event_sink,
            state_event(
                "plan",
                steps=[{"id": value.id, "tool": value.tool} for value in working],
            ),
        )
    return state


def assign_specialist_budget(steps: list[PlanStep]) -> int | None:
    eligible = [step for step in steps if ToolName(step.tool) in _SPECIALIST_TOOLS]
    if not eligible:
        return None
    referenced_ids = {
        reference[0]
        for step in steps
        for _, value in _walk_values(step.args)
        if isinstance(value, str)
        and (reference := parse_step_reference(value)) is not None
    }
    referenced_eligible = [step for step in eligible if step.id in referenced_ids]
    if referenced_eligible:
        # plan の並びが優先順位。数値 ID の大小ではない。
        return referenced_eligible[0].id
    return eligible[-1].id


def resolve_step_references(
    step: PlanStep,
    results: dict[int, Any],
) -> dict[str, Any]:
    """前段 ToolResult の公開フィールドだけを再帰的に埋め込む。"""

    def resolve(value: Any, path: tuple[Any, ...]) -> Any:
        if isinstance(value, dict):
            return {key: resolve(child, (*path, key)) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child, (*path, index)) for index, child in enumerate(value)]
        if not isinstance(value, str) or not value.startswith("$"):
            return value
        parsed = parse_step_reference(value)
        if parsed is None:
            raise ReferenceResolutionError(f"未定義の参照記法です: {value}")
        source_id, field, limit = parsed
        if source_id not in results:
            raise ReferenceResolutionError(f"手 {source_id} の結果がありません")
        source = results[source_id]
        data = source.data if hasattr(source, "data") else source
        if not isinstance(data, dict) or field not in data:
            raise ReferenceResolutionError(f"手 {source_id} は {field} を返していません")
        resolved = deepcopy(data[field])
        if field == "spot_ids":
            if not isinstance(resolved, list) or not all(
                isinstance(item, str) for item in resolved
            ):
                raise ReferenceResolutionError(f"手 {source_id}.spot_ids の型が不正です")
            if limit is not None:
                resolved = resolved[:limit]
            if not resolved:
                raise ReferenceResolutionError(f"{value} は空です")
            if path and path[-1] == "with":
                if limit != 1:
                    raise ReferenceResolutionError("replace.with は [:1] 参照が必要です")
                return resolved[0]
        return resolved

    return resolve(step.args, ())


def _p2_known_tool(step: PlanStep) -> tuple[bool, str]:
    try:
        ToolName(step.tool)
    except ValueError:
        return False, f"未知の Tool です: {step.tool}"
    return True, ""


def _normalize_llm_step(state: TurnState, step: PlanStep) -> PlanStep:
    """LLM が省略しやすい Tool 引数の外形だけを P3 前に補正する。"""

    args = deepcopy(step.args)
    if step.tool == ToolName.PLAN_ITINERARY.value:
        days = args.get("days")
        if isinstance(days, list):
            for day in days:
                if not isinstance(day, dict):
                    continue
                for field in ("origin", "destination"):
                    spot_id = day.get(field)
                    if not isinstance(spot_id, str):
                        continue
                    spot = state.spot_catalog.get(spot_id)
                    kind = (
                        "facility"
                        if spot is not None and spot.kind == "facility"
                        else "spot"
                    )
                    day[field] = {"kind": kind, "id": spot_id}
    elif step.tool == ToolName.EDIT_ITINERARY.value:
        operations = args.get("ops")
        if isinstance(operations, list):
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                if operation.get("op") not in {"remove", "lock"}:
                    continue
                targets = operation.get("targets")
                if isinstance(targets, str) and not targets.startswith("$"):
                    operation["targets"] = [targets]
    return step.model_copy(update={"args": args}, deep=True)


def _parse_args(step: PlanStep) -> dict[str, Any]:
    tool = ToolName(step.tool)
    if tool is ToolName.RECOMMEND:
        parsed = RecommendArgs.model_validate(step.args)
        parsed_filter = RecommendFilter.model_validate(parsed.filter)
        return {
            "filter": parsed_filter.model_dump(mode="json", exclude_none=True),
            "k": parsed.k,
            "exclude": list(dict.fromkeys(parsed.exclude)),
        }
    if tool is ToolName.PLAN_ITINERARY:
        parsed = PlanItineraryArgs.model_validate(step.args)
        days = [_validate_day_shape(value) for value in parsed.days]
        return {"days": days, "must_visit": list(dict.fromkeys(parsed.must_visit))}
    if tool is ToolName.EDIT_ITINERARY:
        parsed = EditItineraryArgs.model_validate(step.args)
        operations = [
            value.model_dump(mode="json", by_alias=True)
            for value in parse_ops(parsed.ops)
        ]
        operations, _ = normalize_revert_ops(operations)
        return {"ops": operations}
    if tool is ToolName.SEARCH_KNOWLEDGE:
        return SearchKnowledgeArgs.model_validate(step.args).model_dump(
            mode="json", exclude_none=True
        )
    if tool is ToolName.ASK_USER:
        parsed = AskUserArgs.model_validate(step.args)
        options: list[dict[str, str]] = []
        seen_values: set[str] = set()
        for option in parsed.options:
            value = option.value.strip()
            if value in seen_values:
                continue
            seen_values.add(value)
            options.append({"label": option.label.strip(), "value": value})
        return {
            "kind": parsed.kind,
            "slot": parsed.slot.value if parsed.slot is not None else None,
            "surface": parsed.surface.strip() if parsed.surface is not None else None,
            "reason": parsed.reason.strip(),
            "options": options,
        }
    raise ValueError(f"未対応の Tool です: {tool}")  # pragma: no cover


def _validate_day_shape(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {"date", "start", "end", "start_min", "end_min", "origin", "destination"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"days に未知フィールドがあります: {sorted(unknown)}")
    result = deepcopy(value)
    if "start" in result or "start_min" in result:
        parse_minute(result.get("start", result.get("start_min")), field_name="start")
    if "end" in result or "end_min" in result:
        parse_minute(result.get("end", result.get("end_min")), field_name="end")
    for field in ("origin", "destination"):
        endpoint = result.get(field)
        if endpoint is None:
            continue
        if not isinstance(endpoint, dict):
            raise ValueError(f"{field} は object にしてください")
        if set(endpoint) - {"kind", "id", "spot_id", "lat", "lon"}:
            raise ValueError(f"{field} に未知フィールドがあります")
        if endpoint.get("kind") not in {"spot", "facility", "coord"}:
            raise ValueError(f"{field}.kind が未定義です")
    return result


def _p4_filter(steps: list[PlanStep], rejected: list[RejectedStep]) -> list[PlanStep]:
    result: list[PlanStep] = []
    known_by_id: dict[int, PlanStep] = {}
    for step in steps:
        if step.id < 1:
            _reject(rejected, step, "P4", "step.id は 1 以上である必要があります")
            continue
        if step.id in known_by_id:
            _reject(rejected, step, "P4", f"step.id が重複しています: {step.id}")
            continue
        error = _reference_error(step, known_by_id)
        if error is not None:
            _reject(rejected, step, "P4", error)
            continue
        known_by_id[step.id] = step
        result.append(step)
    # 前方参照を強制済みだが、将来の語彙追加に備えて循環も検査する。
    cyclic_ids = _cyclic_step_ids(result)
    if cyclic_ids:
        kept: list[PlanStep] = []
        for step in result:
            if step.id in cyclic_ids:
                _reject(rejected, step, "P4", "step 参照が循環しています")
            else:
                kept.append(step)
        return kept
    return result


def _reference_error(step: PlanStep, previous: dict[int, PlanStep]) -> str | None:
    for path, value in _walk_values(step.args):
        if not isinstance(value, str) or not value.startswith("$"):
            continue
        reference = parse_step_reference(value)
        if reference is None:
            return f"未定義の参照記法です: {value}"
        source_id, field, limit = reference
        if source_id not in previous:
            return f"前方参照ではないか、参照先が無効です: {value}"
        source_tool = ToolName(previous[source_id].tool)
        if field == "spot_ids" and source_tool not in _SPOT_ID_OUTPUT_TOOLS:
            return f"{source_tool.value} は spot_ids を返しません"
        if field == "itinerary" and source_tool not in _ITINERARY_OUTPUT_TOOLS:
            return f"{source_tool.value} は itinerary を返しません"
        expected = _expected_reference_field(path)
        if expected is None:
            return f"この引数位置では参照を使えません: {value}"
        if expected == "spot_id" and not (field == "spot_ids" and limit == 1):
            return f"単一 spot_id の位置には $N.spot_ids[:1] が必要です: {value}"
        if expected == "spot_ids" and field != "spot_ids":
            return f"spot_id 配列の位置に itinerary は渡せません: {value}"
        if expected == "itinerary" and field != "itinerary":
            return f"itinerary の位置に spot_ids は渡せません: {value}"
    return None


def _expected_reference_field(path: tuple[Any, ...]) -> str | None:
    if path and path[-1] == "targets":
        return "spot_ids"
    if path and path[-1] == "with":
        return "spot_id"
    if path and path[-1] == "itinerary":
        return "itinerary"
    return None


def _apply_preconditions(
    state: TurnState,
    steps: list[PlanStep],
    rejected: list[RejectedStep],
    *,
    now: datetime | None,
) -> list[PlanStep]:
    result: list[PlanStep] = []
    has_non_question = any(step.tool != ToolName.ASK_USER.value for step in steps)
    ask_count = 0
    needs_confirmation: str | None = None
    for step in steps:
        tool = ToolName(step.tool)
        closed_world_error = _closed_world_error(state, step)
        if closed_world_error is not None:
            _reject(rejected, step, "precondition", closed_world_error)
            continue
        if tool is ToolName.EDIT_ITINERARY and state.itinerary is None:
            _reject(
                rejected,
                step,
                "precondition",
                "編集できる旅程がまだありません",
            )
            continue
        if tool is ToolName.PLAN_ITINERARY and state.itinerary is not None:
            _reject(
                rejected,
                step,
                "precondition",
                (
                    "既に旅程があります。"
                    "変更には edit_itinerary を使ってください"
                ),
            )
            continue
        if tool is ToolName.EDIT_ITINERARY and _contains_revert(step):
            if state.itinerary is None or state.itinerary.version < 2:
                _reject(
                    rejected,
                    step,
                    "precondition",
                    "戻せる旅程の変更がありません",
                )
                continue
        if tool is ToolName.RECOMMEND and state.itinerary is None:
            filter_value = dict(step.args.get("filter", {}))
            if "day" in filter_value:
                filter_value.pop("day")
                step = step.model_copy(
                    update={"args": {**step.args, "filter": filter_value}},
                    deep=True,
                )
        if tool is ToolName.PLAN_ITINERARY:
            try:
                args, assumptions, confirmation_slot = _fill_plan_defaults(
                    state,
                    step.args,
                    now=now,
                )
            except ValueError as exc:
                _reject(rejected, step, "precondition", str(exc))
                continue
            step = step.model_copy(update={"args": args}, deep=True)
            state.assumptions.extend(assumptions)
            needs_confirmation = confirmation_slot or needs_confirmation
        if tool is ToolName.ASK_USER:
            ask_count += 1
            question = AskUserArgs.model_validate(step.args)
            decision = validate_ask_user(
                question,
                asked_slots=state.asked_slots,
                ask_streak=state.ask_streak,
                intent=state.intent,
                profile=state.profile,
                has_non_question_step=has_non_question,
                question_count=ask_count,
                allowed_spot_ids=set(state.spot_id_vocab),
                existing_spot_ids=set(state.spot_catalog),
                resolved_ambiguities=state.resolved_ambiguities,
                has_viable_plan=_has_viable_plan(state, steps, question),
            )
            if not decision.accepted:
                if decision.rule == "G4":
                    # 手ぶらにせず、決定的推薦を先に見せてから確認する。
                    synthetic_id = _next_step_id([*steps, *result])
                    result.append(
                        PlanStep(
                            id=synthetic_id,
                            tool=ToolName.RECOMMEND.value,
                            args={"filter": {}, "k": 5, "exclude": []},
                        )
                    )
                    has_non_question = True
                else:
                    _reject(
                        rejected,
                        step,
                        decision.rule or "precondition",
                        decision.reason or "ask_user の事前条件を満たしません",
                    )
                    if decision.rule == "G3":
                        _record_first_option_assumption(state, question)
                    continue
        result.append(step)

    if needs_confirmation and not any(
        step.tool == ToolName.ASK_USER.value for step in result
    ):
        confirmation = PlanStep(
            id=_next_step_id([*steps, *result]),
            tool=ToolName.ASK_USER.value,
            args={
                "kind": "preference",
                "slot": needs_confirmation,
                "reason": "仮定した旅程条件の確認",
                "options": [
                    {"label": "この条件で進める", "value": "accept_assumptions"},
                    {"label": "条件を変更する", "value": "change_conditions"},
                ],
            },
        )
        decision = validate_ask_user(
            AskUserArgs.model_validate(confirmation.args),
            asked_slots=state.asked_slots,
            ask_streak=state.ask_streak,
            intent=state.intent,
            profile=state.profile,
            has_non_question_step=True,
            allowed_spot_ids=set(state.spot_id_vocab),
            existing_spot_ids=set(state.spot_catalog),
            resolved_ambiguities=state.resolved_ambiguities,
        )
        if decision.accepted:
            result.append(confirmation)
        else:
            _reject(
                rejected,
                confirmation,
                decision.rule or "precondition",
                decision.reason or "確認質問を追加できませんでした",
            )
    return result


def _has_viable_plan(
    state: TurnState,
    steps: list[PlanStep],
    question: AskUserArgs,
) -> bool:
    non_questions = [step for step in steps if step.tool != ToolName.ASK_USER.value]
    if question.kind == "clarify":
        return bool(non_questions) or state.intent not in {None, Intent.UNCLEAR}
    return bool(non_questions) and state.intent in {
        Intent.QA,
        Intent.PROFILE_ONLY,
        Intent.CHITCHAT,
    }


def _record_first_option_assumption(
    state: TurnState,
    question: AskUserArgs,
) -> None:
    if not question.options:
        return
    first = question.options[0]
    subject = question.surface if question.kind == "clarify" else question.slot
    subject_text = subject.value if hasattr(subject, "value") else str(subject or "質問")
    state.assumptions.append(f"「{subject_text}」は {first.label} と仮定しました")
    if (
        question.kind == "clarify"
        and question.surface is not None
        and first.value in state.spot_catalog
        and first.value in state.spot_id_vocab
    ):
        state.references.append(
            ReferenceResolution(surface=question.surface, spot_id=first.value)
        )


def _fill_plan_defaults(
    state: TurnState,
    args: dict[str, Any],
    *,
    now: datetime | None,
) -> tuple[dict[str, Any], list[str], str | None]:
    current = now or datetime.now(_JAPAN_TZ)
    tomorrow = (current.date() + timedelta(days=1)).isoformat()
    origin = state.default_origin_spot_id
    if origin is None:
        raise ValueError("旅程の既定起点に使える spot_id がありません")
    raw_days = deepcopy(args.get("days") or [{}])
    assumptions: list[str] = []
    confirmation_slot: str | None = None
    filled: list[dict[str, Any]] = []
    for raw in raw_days:
        day = dict(raw)
        if not day.get("date"):
            day["date"] = tomorrow
            assumptions.append(f"日付を {tomorrow} と仮定しました")
            confirmation_slot = "dates"
        if "start" not in day and "start_min" not in day:
            day["start"] = "09:00"
            assumptions.append("開始時刻を 09:00 と仮定しました")
            confirmation_slot = "dates"
        if "end" not in day and "end_min" not in day:
            day["end"] = "17:00"
            assumptions.append("終了時刻を 17:00 と仮定しました")
            confirmation_slot = "dates"
        if not day.get("origin"):
            origin_fact = state.spot_catalog.get(origin)
            origin_kind = (
                "facility"
                if origin_fact is not None and origin_fact.kind == "facility"
                else "spot"
            )
            day["origin"] = {"kind": origin_kind, "id": origin}
            assumptions.append(
                f"起点を {state.spot_names.get(origin, origin)} と仮定しました"
            )
            confirmation_slot = confirmation_slot or "origin"
        if not day.get("destination"):
            day["destination"] = deepcopy(day["origin"])
        filled.append(day)
    return (
        {"days": filled, "must_visit": args.get("must_visit", [])},
        assumptions,
        confirmation_slot,
    )


def _closed_world_error(state: TurnState, step: PlanStep) -> str | None:
    allowed = set(state.spot_id_vocab)
    existing = set(state.spot_catalog)
    default_origin = state.default_origin_spot_id
    if default_origin is not None:
        allowed.add(default_origin)
    known_tags = {
        tag for spot in state.spot_catalog.values() for tag in spot.tags_ja
    }
    tool = ToolName(step.tool)
    ids: list[str] = []
    if tool is ToolName.RECOMMEND:
        filter_value = step.args.get("filter", {})
        if isinstance(filter_value, dict):
            tags = filter_value.get("tags", [])
            if isinstance(tags, list):
                unknown_tags = [value for value in tags if value not in known_tags]
                if unknown_tags:
                    return f"実在しない生タグです: {unknown_tags}"
        ids.extend(_strings(step.args.get("exclude")))
    elif tool is ToolName.PLAN_ITINERARY:
        ids.extend(_strings(step.args.get("must_visit")))
        for day in step.args.get("days", []):
            if not isinstance(day, dict):
                continue
            for field in ("origin", "destination"):
                endpoint = day.get(field)
                if isinstance(endpoint, dict):
                    value = endpoint.get("id", endpoint.get("spot_id"))
                    if isinstance(value, str):
                        ids.append(value)
    elif tool is ToolName.EDIT_ITINERARY:
        for operation in step.args.get("ops", []):
            if not isinstance(operation, dict):
                continue
            for field in ("targets", "target", "after", "with"):
                value = operation.get(field)
                if isinstance(value, str) and not value.startswith("$"):
                    ids.append(value)
                elif isinstance(value, list):
                    ids.extend(item for item in value if isinstance(item, str))
    elif tool is ToolName.SEARCH_KNOWLEDGE:
        value = step.args.get("spot_id")
        if isinstance(value, str):
            ids.append(value)
    for spot_id in ids:
        if spot_id not in existing:
            return f"実在しない spot_id です: {spot_id}"
        if spot_id not in allowed:
            return f"現在の文脈から参照できない spot_id です: {spot_id}"
    return None


def _cyclic_step_ids(steps: list[PlanStep]) -> set[int]:
    edges = {
        step.id: {
            reference[0]
            for _, value in _walk_values(step.args)
            if isinstance(value, str)
            and (reference := parse_step_reference(value)) is not None
        }
        for step in steps
    }
    visiting: set[int] = set()
    visited: set[int] = set()
    cyclic: set[int] = set()

    def visit(node: int) -> None:
        if node in visiting:
            cyclic.update(visiting)
            return
        if node in visited:
            return
        visiting.add(node)
        for target in edges.get(node, set()):
            if target in edges:
                visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        visit(node)
    return cyclic


def _walk_values(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], Any]]:
    result: list[tuple[tuple[Any, ...], Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            result.extend(_walk_values(child, (*path, key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(_walk_values(child, (*path, index)))
    else:
        result.append((path, value))
    return result


def _filter_steps(
    steps: list[PlanStep],
    rejected: list[RejectedStep],
    predicate: Any,
) -> list[PlanStep]:
    result: list[PlanStep] = []
    for step in steps:
        accepted, reason = predicate(step)
        if accepted:
            result.append(step)
        else:
            _reject(rejected, step, "P2", reason)
    return result


def _reject(
    rejected: list[RejectedStep],
    step: PlanStep,
    rule: str,
    reason: str,
) -> None:
    rejected.append(
        RejectedStep(
            step_id=step.id,
            tool=step.tool,
            rule=rule,
            reason=reason,
            step=step.model_dump(mode="json"),
        )
    )


def _contains_revert(step: PlanStep) -> bool:
    return any(
        isinstance(value, dict) and value.get("op") == "revert"
        for value in step.args.get("ops", [])
    )


def _mixed_revert_ops(step: PlanStep) -> bool:
    if step.tool != ToolName.EDIT_ITINERARY.value:
        return False
    operations = step.args.get("ops", [])
    if not isinstance(operations, list):
        return False
    revert_count = sum(
        isinstance(value, dict) and value.get("op") == "revert"
        for value in operations
    )
    return revert_count > 0 and (len(operations) != 1 or revert_count != 1)


def _next_step_id(steps: list[PlanStep]) -> int:
    return max((step.id for step in steps), default=0) + 1


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
