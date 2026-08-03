"""N1.5 `update_profile`: 履歴 + 最新発話からプロフィール差分を1回の
guided JSON で抽出し、`state.profile` へ即座にマージするステップ。

`Docs/30_design/agent_react_architecture.md` §2 が仕様である。
- 差分が空なら `profiles` に書かず、`state: profile` イベントも送出しない
  （[Docs/23_ux_issues.md](../../../../Docs/23_ux_issues.md) §3-5 の解消）
- `score_adjustments` はそのターン限りで、`profiles` へは書かない
  （`state.profile_delta` / `state.score_adjustments` は永続化しない）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.llm import GenerationClient
from app.domains.conversation.events import EventSinkLike, emit, state_event
from app.domains.conversation.guards import has_repeated_ngram
from app.domains.conversation.prompts import (
    build_update_profile_messages,
    update_profile_guided_schema,
)
from app.domains.conversation.state import DegradedState, ProfileState, TurnState
from app.domains.conversation.types import ProfileDelta, ScoreAdjustment, UpdateProfileOutput


class GenerationPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


logger = logging.getLogger("app.conversation.update_profile")

UPDATE_PROFILE_EXPECTED_TOKENS = 400
UPDATE_PROFILE_MAX_TOKENS = int(UPDATE_PROFILE_EXPECTED_TOKENS * 1.5)
UPDATE_PROFILE_WALLCLOCK_SEC = 60.0


def is_profile_delta_empty(delta: ProfileDelta | None) -> bool:
    """差分が実質空かどうか。空 dict/空 list/None だけの ProfileDelta を空とみなす。"""

    if delta is None:
        return True
    return (
        not delta.interests
        and delta.party is None
        and delta.mobility is None
        and delta.pace is None
        and not delta.avoid
        and not delta.notes
    )


def merge_profile_delta(profile: ProfileState, delta: ProfileDelta) -> ProfileState:
    """恒久的なプロフィールへ差分を上書きマージする（interests/avoid は追記）。"""

    interests = dict(profile.interests)
    interests.update({key.value: value for key, value in delta.interests.items()})
    return profile.model_copy(
        update={
            "interests": interests,
            "party": delta.party.value if delta.party is not None else profile.party,
            "mobility": (
                delta.mobility.value if delta.mobility is not None else profile.mobility
            ),
            "pace": delta.pace.value if delta.pace is not None else profile.pace,
            "avoid": list(dict.fromkeys([*profile.avoid, *delta.avoid])),
            "notes": delta.notes if delta.notes is not None else profile.notes,
        },
        deep=True,
    )


async def update_profile(
    state: TurnState,
    *,
    client: GenerationPort | None = None,
    event_sink: EventSinkLike = None,
    wallclock_sec: float = UPDATE_PROFILE_WALLCLOCK_SEC,
    utterance_override: str | None = None,
) -> TurnState:
    """`state.profile` / `profile_delta` / `score_adjustments` を埋める。

    guided decoding や通信が失敗しても例外は上げず、このターンは差分なし
    として続行する（後続ノードを止めない。NFR-5）。

    `utterance_override` を渡すと、`ask_user` の回答文に対して本ステップを
    ターン内でもう 1 回実行できる(§2・§7: 回答 → プロフィール更新 →
    更新後プロフィールで続行)。
    """

    resolved_client = client or GenerationClient()
    schema = update_profile_guided_schema(state.spot_id_vocab)
    messages = build_update_profile_messages(state, utterance_override=utterance_override)
    started_at = time.perf_counter()
    output: UpdateProfileOutput | None = None
    failure: str | None = None
    try:
        async with asyncio.timeout(wallclock_sec):
            raw = await resolved_client.generate(
                messages,
                temperature=0.0,
                max_tokens=UPDATE_PROFILE_MAX_TOKENS,
                extra_body={
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "conversation_update_profile",
                            "strict": True,
                            "schema": schema,
                        },
                    }
                },
            )
        if has_repeated_ngram(raw):
            raise ValueError("同一 n-gram の反復を検知しました")
        output = UpdateProfileOutput.model_validate(json.loads(raw))
    except asyncio.CancelledError:
        # キャンセルは呼び出し元（pipeline）へ伝播させ、persist に到達させる。
        raise
    except Exception as exc:  # noqa: BLE001 - 補助ステップの失敗で対話を止めない
        failure = f"{type(exc).__name__}: {exc}"

    state.log_fields["update_profile_ms"] = round(
        (time.perf_counter() - started_at) * 1000, 2
    )
    if output is None:
        state.log_fields["update_profile_failed"] = True
        logger.warning(
            "update_profile_failed",
            extra={"turn_id": state.turn_id, "reason": failure},
        )
        state.degraded.append(
            DegradedState(
                code="update_profile_failed",
                stage="update_profile",
                message="プロフィール更新の抽出に失敗しました",
            )
        )
        return state

    delta = None if is_profile_delta_empty(output.profile_delta) else output.profile_delta
    state.profile_delta = delta
    state.score_adjustments = _valid_score_adjustments(state, output.score_adjustments)

    if delta is not None:
        state.profile = merge_profile_delta(state.profile, delta)
        await emit(
            event_sink,
            state_event("profile", profile=state.profile.model_dump(mode="json")),
        )
    return state


def _valid_score_adjustments(
    state: TurnState, values: list[ScoreAdjustment]
) -> list[ScoreAdjustment]:
    allowed = set(state.spot_id_vocab)
    existing = set(state.spot_catalog)
    seen: set[str] = set()
    result: list[ScoreAdjustment] = []
    for value in values:
        if value.spot_id in seen:
            continue
        if value.spot_id in allowed and value.spot_id in existing:
            seen.add(value.spot_id)
            result.append(value)
    return result
