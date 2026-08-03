"""① `load_context` とコンテキスト予算の組み立て。

段2(ReAct 化)で `ask_user` の中断・復帰路(旧 `_resume_pending_ask`)は削除した
(`Docs/30_design/agent_react_architecture.md` §7: 段5で `ask_user` は
ターンを中断しない通常の Tool として作り直す)。`pending_ask` 列自体は
段5で使うため `ContextSnapshot`/`TurnState` に残すが、ここでは素通りさせる
だけで、ターン開始時に Tool 結果へ復元する処理は持たない。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import uuid4

from app.domains.conversation.history import build_conversation_history
from app.domains.conversation.state import (
    ContextSnapshot,
    SpotFact,
    TurnState,
)


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

    del resolves  # 段5で `POST /chat/answer` の解決値として使う。段2では未使用。
    snapshot = await repository.load_snapshot(user_id)
    history = build_conversation_history(
        snapshot.messages,
        history_summary=snapshot.history_summary,
        summarized_until_message_id=snapshot.summarized_until_message_id,
    )
    vocabulary = _spot_vocabulary(
        snapshot,
        utterance=utterance,
        history_spot_ids=history.mentioned_spot_ids,
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
        log_fields={
            "history_tokens": history.estimated_tokens,
            "history_raw_turns": history.raw_turns,
            "history_summarized_turns": history.summarized_turns,
            "history_candidate_lists": history.candidate_lists,
            "history_dropped_sections": history.dropped_sections,
            "initial_itinerary_version": initial_version,
        },
    )


def _spot_vocabulary(
    snapshot: ContextSnapshot,
    *,
    utterance: str,
    history_spot_ids: tuple[str, ...],
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
