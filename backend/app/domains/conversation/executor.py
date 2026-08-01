"""N4 `act`: 検証済み Tool を順に実行する唯一のノード。"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from app.domains.conversation.events import EventSinkLike, emit, error_event, state_event
from app.domains.conversation.planner import ReferenceResolutionError, resolve_step_references
from app.domains.conversation.state import (
    CandidateReference,
    DegradedState,
    ItineraryState,
    SkippedStep,
    TurnState,
)
from app.domains.conversation.tool_ports import ConversationToolPort
from app.domains.conversation.types import (
    AskUserArgs,
    EditItineraryArgs,
    PlanItineraryArgs,
    RecommendArgs,
    SearchKnowledgeArgs,
    ToolError,
    ToolErrorCode,
    ToolName,
)
from app.domains.itinerary.types import Itinerary
from app.domains.recommendation.types import (
    RecommendationContext,
    RecommendationProfile,
)


async def act(
    state: TurnState,
    *,
    tools: ConversationToolPort,
    event_sink: EventSinkLike = None,
) -> TurnState:
    """`$N`不能=skip、事前条件不満=skip、Tool例外=abort を分離する。"""

    for step in state.accepted_steps:
        try:
            resolved_args = resolve_step_references(step, state.step_results)
        except ReferenceResolutionError as exc:
            state.skipped_steps.append(
                SkippedStep(
                    step_id=step.id,
                    tool=step.tool,
                    code="reference_unresolved",
                    reason=str(exc),
                )
            )
            continue

        precondition = _runtime_precondition(state, ToolName(step.tool), resolved_args)
        if precondition is not None:
            state.skipped_steps.append(
                SkippedStep(
                    step_id=step.id,
                    tool=step.tool,
                    code="precondition_unmet",
                    reason=precondition,
                )
            )
            continue

        started_at = time.perf_counter()
        try:
            result = await _execute_step(
                state,
                tools,
                step.id,
                ToolName(step.tool),
                resolved_args,
            )
        except Exception as exc:  # noqa: BLE001 - Port 外への例外漏れも中止へ閉じる
            result = ToolError(
                code=ToolErrorCode.INTERNAL,
                message_ja="処理中に予期しない問題が発生しました。",
                recoverable=False,
                details={"error_type": type(exc).__name__},
            )
        finally:
            tool_ms = state.log_fields.setdefault("tool_ms", {})
            tool_ms[str(step.id)] = round(
                (time.perf_counter() - started_at) * 1000,
                2,
            )
        if isinstance(result, ToolError):
            state.skipped_steps.append(
                SkippedStep(
                    step_id=step.id,
                    tool=step.tool,
                    code=result.code.value,
                    reason=result.message_ja,
                )
            )
            if result.recoverable:
                continue
            state.aborted_at = step.id
            await emit(
                event_sink,
                error_event(
                    stage="act",
                    code=result.code.value,
                    degraded=False,
                    message=result.message_ja,
                ),
            )
            break

        state.step_results[step.id] = result
        for code in result.degraded:
            state.degraded.append(
                DegradedState(
                    code=code,
                    stage="act",
                    message=f"{step.tool} は縮退経路を使用しました",
                )
            )
        _apply_result(state, result)
        if result.tool is ToolName.ASK_USER:
            state.should_end_turn = True
            state.ask_user_payload = result.data
            break

    state.log_fields["executed_tools"] = [
        result.tool.value for result in state.step_results.values()
    ]
    state.log_fields["skipped_steps"] = [
        value.model_dump(mode="json") for value in state.skipped_steps
    ]
    state.log_fields["aborted_at"] = state.aborted_at
    return state


async def apply_profile_update(
    state: TurnState,
    *,
    event_sink: EventSinkLike = None,
) -> TurnState:
    """N2 の差分を in-memory 状態へ一度だけマージし、state を送出する。"""

    if state.profile_delta is None:
        return state
    delta = state.profile_delta
    interests = dict(state.profile.interests)
    interests.update({key.value: value for key, value in delta.interests.items()})
    state.profile = state.profile.model_copy(
        update={
            "interests": interests,
            "party": delta.party.value if delta.party is not None else state.profile.party,
            "mobility": (
                delta.mobility.value if delta.mobility is not None else state.profile.mobility
            ),
            "pace": delta.pace.value if delta.pace is not None else state.profile.pace,
            "avoid": list(dict.fromkeys([*state.profile.avoid, *delta.avoid])),
            "notes": delta.notes if delta.notes is not None else state.profile.notes,
        },
        deep=True,
    )
    await emit(
        event_sink,
        state_event("profile", profile=state.profile.model_dump(mode="json")),
    )
    return state


async def _execute_step(
    state: TurnState,
    tools: ConversationToolPort,
    step_id: int,
    tool: ToolName,
    args: dict[str, Any],
) -> Any:
    recommendation_context = build_recommendation_context(state)
    use_specialist = state.llm_budget_step == step_id
    if tool is ToolName.RECOMMEND:
        return await tools.recommend(
            step_id=step_id,
            args=RecommendArgs.model_validate(args),
            context=recommendation_context,
            use_specialist=use_specialist,
        )
    if tool is ToolName.PLAN_ITINERARY:
        return await tools.plan_itinerary(
            step_id=step_id,
            user_id=state.user_id,
            args=PlanItineraryArgs.model_validate(args),
            constraints=state.constraints,
            selection_text=_selection_text(state),
            recommendation_context=recommendation_context,
            use_specialist=use_specialist,
        )
    if tool is ToolName.EDIT_ITINERARY:
        return await tools.edit_itinerary(
            step_id=step_id,
            user_id=state.user_id,
            args=EditItineraryArgs.model_validate(args),
            constraints=state.constraints,
            constraints_remove=state.constraints_remove,
            selection_text=_selection_text(state),
            recommendation_context=recommendation_context,
            use_specialist=use_specialist,
        )
    if tool is ToolName.SEARCH_KNOWLEDGE:
        return await tools.search_knowledge(
            step_id=step_id,
            args=SearchKnowledgeArgs.model_validate(args),
        )
    if tool is ToolName.ASK_USER:
        return await tools.ask_user(
            step_id=step_id,
            args=AskUserArgs.model_validate(args),
        )
    raise RuntimeError(f"未対応の Tool です: {tool}")  # pragma: no cover


def build_recommendation_context(state: TurnState) -> RecommendationContext:
    itinerary = state.itinerary.itinerary if state.itinerary is not None else None
    day_dates: dict[int, date] = {}
    day_previous: dict[int, str] = {}
    day_origins: dict[int, str] = {}
    previous_spot_id: str | None = None
    base_spot_id: str | None = state.default_origin_spot_id
    travel_date: date | None = None
    if itinerary is not None:
        for index, day in enumerate(itinerary.days, 1):
            try:
                parsed_date = date.fromisoformat(day.date)
            except ValueError:
                continue
            day_dates[index] = parsed_date
            travel_date = travel_date or parsed_date
            day_origins[index] = day.origin.spot_id
            if day.items:
                day_previous[index] = day.items[-1].spot_id
                previous_spot_id = day.items[-1].spot_id
            base_spot_id = base_spot_id or day.origin.spot_id
    profile_values = state.profile.model_dump(mode="python")
    if state.profile_delta is not None:
        delta = state.profile_delta
        interests = dict(profile_values["interests"])
        interests.update({key.value: value for key, value in delta.interests.items()})
        profile_values.update(
            {
                "interests": interests,
                "party": delta.party.value if delta.party is not None else None,
                "mobility": (
                    delta.mobility.value if delta.mobility is not None else None
                ),
                "pace": delta.pace.value if delta.pace is not None else None,
                "avoid": list(
                    dict.fromkeys([*profile_values["avoid"], *delta.avoid])
                ),
                "notes": delta.notes,
            }
        )
        # 差分にない nullable 値は既存値を保つ。
        for field in ("party", "mobility", "pace", "notes"):
            if profile_values[field] is None:
                profile_values[field] = getattr(state.profile, field)
    profile = RecommendationProfile.model_validate(profile_values)
    return RecommendationContext(
        profile=profile,
        presented_spot_ids=state.presented_spot_ids,
        previous_spot_id=previous_spot_id,
        base_spot_id=base_spot_id,
        travel_date=travel_date,
        day_dates=day_dates,
        day_previous_spot_ids=day_previous,
        day_origin_spot_ids=day_origins,
        score_adjustments={
            value.spot_id: value.delta for value in state.score_adjustments
        },
    )


def _runtime_precondition(
    state: TurnState,
    tool: ToolName,
    args: dict[str, Any],
) -> str | None:
    if tool is ToolName.EDIT_ITINERARY and state.itinerary is None:
        return "編集できる旅程がまだありません"
    if tool is ToolName.EDIT_ITINERARY and any(
        isinstance(value, dict) and value.get("op") == "revert"
        for value in args.get("ops", [])
    ):
        if state.itinerary is None or state.itinerary.version < 2:
            return "戻せる旅程の変更がありません"
    return None


def _apply_result(state: TurnState, result: Any) -> None:
    if result.tool is ToolName.RECOMMEND:
        candidates = result.data.get("candidates", [])
        references: list[CandidateReference] = []
        for index, value in enumerate(candidates, 1):
            if not isinstance(value, dict) or not isinstance(value.get("spot_id"), str):
                continue
            spot_id = value["spot_id"]
            references.append(
                CandidateReference(
                    spot_id=spot_id,
                    name_ja=state.spot_names.get(spot_id, spot_id),
                    rank=int(value.get("rank", index)),
                )
            )
        state.last_candidates = references
        state.presented_spot_ids = list(
            dict.fromkeys(
                [
                    *state.presented_spot_ids,
                    *[
                        value
                        for value in result.data.get("spot_ids", [])
                        if isinstance(value, str)
                    ],
                ]
            )
        )
    elif result.tool in {ToolName.PLAN_ITINERARY, ToolName.EDIT_ITINERARY}:
        raw = result.data.get("itinerary")
        if isinstance(raw, dict):
            itinerary = Itinerary.model_validate(raw)
            constraints = _merged_constraints(state)
            state.itinerary = ItineraryState(
                itinerary=itinerary,
                constraints=constraints,
                parent_version=(
                    state.itinerary.version if state.itinerary is not None else None
                ),
            )


def _merged_constraints(state: TurnState) -> list[dict[str, Any]]:
    existing = (
        list(state.itinerary.constraints)
        if state.itinerary is not None
        else list(state.pending_constraints)
    )
    remove = set(state.constraints_remove)
    kept = [value for value in existing if str(value.get("id")) not in remove]
    kept.extend(value.model_dump(mode="json", exclude_none=True) for value in state.constraints)
    return kept


def _selection_text(state: TurnState) -> str:
    return "\n".join(value.text for value in state.selection_hints)
