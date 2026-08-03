"""N1 `load_context` とコンテキスト予算の組み立て。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Protocol
from uuid import uuid4

from app.domains.conversation.history import build_conversation_history
from app.domains.conversation.state import (
    ContextSnapshot,
    SpotFact,
    TurnState,
)
from app.domains.conversation.types import AskUserResult


class ContextRepositoryPort(Protocol):
    async def load_snapshot(self, user_id: int) -> ContextSnapshot: ...


async def load_context(
    repository: ContextRepositoryPort,
    *,
    user_id: int,
    utterance: str,
    turn_id: str | None = None,
    resolves: Mapping[str, Any] | None = None,
) -> TurnState:
    """DB 由来の全状態を読み、履歴と参照可能語彙を 1 回だけ作る。"""

    snapshot = await repository.load_snapshot(user_id)
    history = build_conversation_history(
        snapshot.messages,
        history_summary=snapshot.history_summary,
        summarized_until_message_id=snapshot.summarized_until_message_id,
    )
    resumed_tool_result = _resume_pending_ask(
        snapshot.pending_ask,
        utterance=utterance,
        resolves=resolves,
    )
    vocabulary = _spot_vocabulary(
        snapshot,
        utterance=utterance,
        history_spot_ids=history.mentioned_spot_ids,
        resumed_tool_result=resumed_tool_result,
    )
    default_origin = _default_origin(snapshot)
    initial_version = (
        snapshot.itinerary.version if snapshot.itinerary is not None else 0
    )
    return TurnState(
        turn_id=turn_id or str(uuid4()),
        thread_id=snapshot.thread_id,
        user_id=user_id,
        utterance=utterance,
        profile=snapshot.profile,
        itinerary=snapshot.itinerary,
        history=history.text,
        history_tokens=history.estimated_tokens,
        last_candidates=snapshot.last_candidates,
        presented_spot_ids=snapshot.presented_spot_ids,
        asked_slots=snapshot.asked_slots,
        ask_streak=snapshot.ask_streak,
        resolved_ambiguities=snapshot.resolved_ambiguities,
        pending_constraints=snapshot.pending_constraints,
        realtime=snapshot.realtime,
        spot_id_vocab=vocabulary,
        spot_names={spot_id: value.name_ja for spot_id, value in snapshot.spots.items()},
        spot_catalog=snapshot.spots,
        tag_vocabulary=list(snapshot.tag_vocabulary),
        default_origin_spot_id=default_origin,
        tool_results=([resumed_tool_result] if resumed_tool_result is not None else []),
        log_fields={
            "history_tokens": history.estimated_tokens,
            "history_raw_turns": history.raw_turns,
            "history_summarized_turns": history.summarized_turns,
            "history_candidate_lists": history.candidate_lists,
            "history_dropped_sections": history.dropped_sections,
            "initial_itinerary_version": initial_version,
            "resumed_from_ask": resumed_tool_result is not None,
        },
    )


def _spot_vocabulary(
    snapshot: ContextSnapshot,
    *,
    utterance: str,
    history_spot_ids: tuple[str, ...],
    resumed_tool_result: dict[str, Any] | None,
) -> list[str]:
    ordered: list[str] = []
    if snapshot.itinerary is not None:
        for day in snapshot.itinerary.itinerary.days:
            ordered.append(day.origin.spot_id)
            ordered.extend(item.spot_id for item in day.items)
            ordered.append(day.destination.spot_id)
    ordered.extend(value.spot_id for value in snapshot.last_candidates)
    ordered.extend(history_spot_ids)
    ordered.extend(_alias_matches(utterance, snapshot.spots))
    if resumed_tool_result is not None:
        output = resumed_tool_result.get("output")
        if isinstance(output, dict) and output.get("answer") in snapshot.spots:
            ordered.append(str(output["answer"]))
    return [
        spot_id
        for spot_id in dict.fromkeys(ordered)
        if spot_id in snapshot.spots
    ]


def _alias_matches(utterance: str, spots: dict[str, SpotFact]) -> list[str]:
    normalized_utterance = _normalize_surface(utterance)
    matches: list[tuple[int, str]] = []
    for spot_id, spot in spots.items():
        aliases = [spot.name_ja, *spot.aliases_ja]
        longest = max(
            (
                len(normalized)
                for alias in aliases
                if (normalized := _normalize_surface(alias))
                and normalized in normalized_utterance
            ),
            default=0,
        )
        if longest:
            matches.append((longest, spot_id))
    # 長い固有名を先に載せ、同長なら DB の安定順にする。
    return [spot_id for _, spot_id in sorted(matches, key=lambda item: (-item[0], item[1]))]


def _normalize_surface(value: str) -> str:
    return re.sub(r"[\s・･]", "", value).casefold()


def _default_origin(snapshot: ContextSnapshot) -> str | None:
    if snapshot.itinerary is not None and snapshot.itinerary.itinerary.days:
        return snapshot.itinerary.itinerary.days[0].origin.spot_id
    facilities = sorted(
        spot_id for spot_id, value in snapshot.spots.items() if value.kind == "facility"
    )
    if facilities:
        return facilities[0]
    return min(snapshot.spots, default=None)


def _resume_pending_ask(
    pending: dict[str, Any] | None,
    *,
    utterance: str,
    resolves: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """生きている pending_ask を、この入力ターンだけ Tool 結果へ復帰する。"""

    if pending is None:
        return None
    kind = pending.get("kind")
    if kind not in {"preference", "clarify"}:
        return None
    slot = pending.get("slot")
    surface = pending.get("surface")
    if kind == "preference" and not isinstance(slot, str):
        return None
    if kind == "clarify" and not isinstance(surface, str):
        return None

    options = _pending_options(pending)
    selected_value: str | None = None
    if resolves is not None:
        resolved_surface = resolves.get("surface")
        resolved_value = resolves.get("value")
        if (
            kind == "clarify"
            and isinstance(resolved_surface, str)
            and resolved_surface == surface
            and isinstance(resolved_value, str)
            and resolved_value in {option["value"] for option in options}
        ):
            selected_value = resolved_value

    output = AskUserResult(
        answer=selected_value or utterance,
        answered_by="chip" if selected_value is not None else "free_text",
        slot=slot if kind == "preference" else None,
        surface=surface if kind == "clarify" else None,
    ).model_dump(mode="json", exclude_none=True)
    tool_input = {
        "kind": kind,
        "reason": str(pending.get("reason", "")),
        "options": options,
    }
    if kind == "preference":
        tool_input["slot"] = slot
    else:
        tool_input["surface"] = surface
    for key in ("original_utterance", "asked_at_message_id"):
        if key in pending:
            tool_input[key] = deepcopy(pending[key])
    return {"tool": "ask_user", "input": tool_input, "output": output}


def _pending_options(pending: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    options = pending.get("options")
    if not isinstance(options, list):
        return result
    for raw in options:
        if not isinstance(raw, Mapping):
            continue
        label = raw.get("label")
        value = raw.get("value")
        if not isinstance(value, str):
            resolution = raw.get("resolves_to")
            if isinstance(resolution, Mapping):
                value = resolution.get("value")
        if isinstance(label, str) and isinstance(value, str):
            result.append({"label": label, "value": value})
    return result
