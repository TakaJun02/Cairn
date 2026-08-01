"""N1 `load_context` とコンテキスト予算の組み立て。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import uuid4

from app.domains.conversation.history import build_conversation_history
from app.domains.conversation.state import (
    ContextSnapshot,
    ExplicitResolution,
    SpotFact,
    TurnState,
)

_INTERPRETATION_VALUES = {
    "all_matches",
    "single_match",
    "current_itinerary",
    "last_candidates",
}


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
    history = build_conversation_history(snapshot.messages)
    explicit_resolution = _validate_explicit_resolution(
        resolves,
        snapshot.pending_clarification,
        snapshot.spots,
    )
    vocabulary = _spot_vocabulary(
        snapshot,
        utterance=utterance,
        history_spot_ids=history.mentioned_spot_ids,
        explicit_resolution=explicit_resolution,
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
        pending_clarification=snapshot.pending_clarification,
        resolved_ambiguities=snapshot.resolved_ambiguities,
        clarify_streak=snapshot.clarify_streak,
        pending_constraints=snapshot.pending_constraints,
        realtime=snapshot.realtime,
        spot_id_vocab=vocabulary,
        spot_names={spot_id: value.name_ja for spot_id, value in snapshot.spots.items()},
        spot_catalog=snapshot.spots,
        default_origin_spot_id=default_origin,
        explicit_resolution=explicit_resolution,
        log_fields={
            "history_tokens": history.estimated_tokens,
            "history_raw_turns": history.raw_turns,
            "history_compressed_turns": history.compressed_turns,
            "history_dropped_turns": history.dropped_turns,
            "initial_itinerary_version": initial_version,
        },
    )


def _spot_vocabulary(
    snapshot: ContextSnapshot,
    *,
    utterance: str,
    history_spot_ids: tuple[str, ...],
    explicit_resolution: ExplicitResolution | None,
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
    if explicit_resolution is not None:
        if explicit_resolution.value in snapshot.spots:
            ordered.append(explicit_resolution.value)
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


def _validate_explicit_resolution(
    resolves: Mapping[str, Any] | None,
    pending: dict[str, Any] | None,
    spots: dict[str, SpotFact],
) -> ExplicitResolution | None:
    if resolves is None or pending is None:
        return None
    surface = resolves.get("surface")
    value = resolves.get("value")
    if not isinstance(surface, str) or not isinstance(value, str):
        return None
    if surface != pending.get("surface"):
        return None
    allowed: set[str] = set()
    options = pending.get("options")
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            resolution = option.get("resolves_to")
            if isinstance(resolution, dict) and isinstance(resolution.get("value"), str):
                allowed.add(resolution["value"])
            legacy_value = option.get("value")
            if isinstance(legacy_value, str):
                allowed.add(legacy_value)
    if value not in allowed:
        return None
    if value not in spots and value not in _INTERPRETATION_VALUES:
        return None
    return ExplicitResolution(surface=surface, value=value)
