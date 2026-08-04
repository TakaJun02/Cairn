"""① `load_context` とコンテキスト予算の組み立て。

`ask_user` はターンを中断しない通常の Tool である(§7)。回答は
`POST /api/v1/chat/answer` から `ask_registry` 経由で実行中のターンへ直接
届き(`tool_adapters.ToolAdapters.ask_user` が待っている)、`load_context` を
経由しない。`pending_ask` はリロード復元専用の表示状態(§7)で、ここでは
素通りさせるだけで、ターン開始時に Tool 結果へ復元する処理は持たない。
"""

from __future__ import annotations

import re
from typing import Protocol
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
) -> TurnState:
    """DB 由来の全状態を読み、履歴と参照可能語彙を 1 回だけ作る。"""

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
    """既存旅程の 1 日目の起点だけを既定値にする(2026-08-04、[25 §1-4]の是正)。

    旧実装は既存旅程が無いとき facility 種別のソート順先頭
    (`min(snapshot.spots)` へのフォールバックまで)を暗黙の既定起点として
    返しており、確認していない起点基準の所要時間・旅程が黙って作られていた
    (Docs/30_design/recommendation_planning.md §3.3、Docs/30_design/
    agent_react_architecture.md §5)。**フォールバックは削除する**: 既存旅程
    が無ければ `None` を返し、起点はユーザーに確認するか会話から得た地点名を
    明示させる。
    """

    if snapshot.itinerary is not None and snapshot.itinerary.itinerary.days:
        return snapshot.itinerary.itinerary.days[0].origin.spot_id
    return None
