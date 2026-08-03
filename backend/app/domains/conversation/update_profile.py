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
import re
import time
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.llm import GenerationClient
from app.domains.conversation.events import EventSinkLike, emit, state_event
from app.domains.conversation.guards import has_repeated_ngram
from app.domains.conversation.prompts import (
    build_update_profile_fallback_messages,
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
# 失敗時にログへ残す生出力の先頭何字か(不具合1の調査可能性のため)。
RAW_OUTPUT_LOG_HEAD = 500

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|```\s*$")


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
    output, failure, raw_head = await _generate_update_profile(
        resolved_client, messages, schema, wallclock_sec=wallclock_sec
    )

    state.log_fields["update_profile_ms"] = round(
        (time.perf_counter() - started_at) * 1000, 2
    )
    if output is None:
        state.log_fields["update_profile_failed"] = True
        logger.warning(
            "update_profile_failed",
            extra={
                "turn_id": state.turn_id,
                "reason": failure,
                # 不具合1の是正: 生出力の先頭を残し、再発時に原因を追えるようにする。
                "raw_output_head": raw_head,
            },
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
    # 2026-08-04 レビュー是正(High・裁定13): 置換ではなくマージする。
    # `ask_user` の回答に対して本ステップをターン内でもう 1 回走らせると
    # (§2・§7)、以前は毎回まるごと置き換えていたため、ターン冒頭の発話
    # 由来の score_adjustments(そのターン限りの点数補正)が回答後の再実行で
    # 消えていた。同じ spot_id は後勝ちで上書きし、それ以外は両方残す。
    state.score_adjustments = _merge_score_adjustments(
        state.score_adjustments, _valid_score_adjustments(state, output.score_adjustments)
    )

    if delta is not None:
        state.profile = merge_profile_delta(state.profile, delta)
        await emit(
            event_sink,
            state_event("profile", profile=state.profile.model_dump(mode="json")),
        )
    return state


async def _generate_update_profile(
    client: GenerationPort,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    wallclock_sec: float,
) -> tuple[UpdateProfileOutput | None, str | None, str | None]:
    """guided decoding を 1 回試し、壊れていたら guided を外して 1 回だけ再試行する。

    2026-08-04 実機調査(不具合1): 実機ログで「edit 系の発話(『胴腹滝は
    外してください』『やっぱりさっきのプランに戻してください』)」の後に
    `update_profile_failed reason="JSONDecodeError: Expecting ',' delimiter..."`
    が断続的に出ていた件を、127.0.0.1:8000(google/gemma-4-31B-it-qat-w4a16-ct)
    へ本番相当のプロンプト・schema を直接投げて再現した。

    判明した原因: vLLM/xgrammar の guided decoding が、この schema
    (`score_adjustments[].delta` のような、配列要素内で他の必須プロパティに
    先立つ number フィールド)とこの system prompt の組み合わせで、
    数値を書き終えた直後から **空白トークンだけを無限に出力し続ける**
    (`finish_reason="length"` まで戻ってこない真の無限ループ。
    max_tokens を 4000 まで上げても解消しなかった)。
    以下はいずれも試したが解消しなかった:
      - `delta`/`interests` の `minimum`/`maximum` を外す
      - `delta` を範囲付き `enum` にする(離散値でも同じ箇所で詰まる)
      - `score_adjustments` を `maxItems: 0` にして配列自体を潰す
        (それでも `"score_adjustments": [` の直後で同じ空白ループに入る)
      - `temperature` を 0.4/0.7 に上げる
      - 契約違反を指摘する文言をプロンプトに追記する(`main_agent` と同じ
        再試行パターン)
    一方、**同じメッセージ・同じ temperature=0.0 のまま `response_format`
    (guided decoding)を外すだけ**で、`finish_reason="stop"` の正しい JSON
    が即座に返ることを確認した。そのため本関数は、1 回目(guided)が壊れたら
    2 回目は guided を外し、`UPDATE_PROFILE_FALLBACK_NOTE`
    (`prompts.build_update_profile_fallback_messages`)でスキーマ相当の
    指示をテキストとして与えて再試行する。guided 前提を外すぶん、
    `score_adjustments.delta`/`interests.*` の範囲外値は
    `types.py` の validator 側でクランプする(範囲外を理由に丸ごと
    失敗させない)。

    戻り値は `(output, failure_reason, raw_output_head)`。失敗時は
    `raw_output_head` に生出力の先頭 `RAW_OUTPUT_LOG_HEAD` 字を積み、
    呼び出し元がログへ残す(調査可能性のため)。
    """

    output, first_failure, first_raw = await _attempt_guided(
        client, messages, schema, wallclock_sec=wallclock_sec
    )
    if output is not None:
        return output, None, None

    fallback_messages = build_update_profile_fallback_messages(
        messages, reason=first_failure or "unknown"
    )
    output, second_failure, second_raw = await _attempt_fallback(
        client, fallback_messages, wallclock_sec=wallclock_sec
    )
    if output is not None:
        return output, None, None

    combined_failure = f"1回目(guided): {first_failure} / 2回目(fallback): {second_failure}"
    combined_raw = f"1回目: {first_raw!r} / 2回目: {second_raw!r}"
    return None, combined_failure, combined_raw


async def _attempt_guided(
    client: GenerationPort,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    wallclock_sec: float,
) -> tuple[UpdateProfileOutput | None, str | None, str | None]:
    raw = ""
    try:
        async with asyncio.timeout(wallclock_sec):
            raw = await client.generate(
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
        return UpdateProfileOutput.model_validate(json.loads(raw)), None, None
    except asyncio.CancelledError:
        # キャンセルは呼び出し元（pipeline）へ伝播させ、persist に到達させる。
        raise
    except Exception as exc:  # noqa: BLE001 - 補助ステップの失敗で対話を止めない
        return None, f"{type(exc).__name__}: {exc}", raw[:RAW_OUTPUT_LOG_HEAD]


async def _attempt_fallback(
    client: GenerationPort,
    messages: list[dict[str, str]],
    *,
    wallclock_sec: float,
) -> tuple[UpdateProfileOutput | None, str | None, str | None]:
    """guided decoding を外した再試行(不具合1)。schema はテキスト指示で渡す。"""

    raw = ""
    try:
        async with asyncio.timeout(wallclock_sec):
            raw = await client.generate(
                messages,
                temperature=0.0,
                max_tokens=UPDATE_PROFILE_MAX_TOKENS,
            )
        if has_repeated_ngram(raw):
            raise ValueError("同一 n-gram の反復を検知しました")
        extracted = _extract_json_object(raw)
        return UpdateProfileOutput.model_validate(json.loads(extracted)), None, None
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 補助ステップの失敗で対話を止めない
        return None, f"{type(exc).__name__}: {exc}", raw[:RAW_OUTPUT_LOG_HEAD]


def _extract_json_object(text: str) -> str:
    """コードフェンスを剥がし、最初の JSON オブジェクトの範囲を取り出す。

    guided decoding を外した再試行は出力形式の保証が無いため、説明文や
    ```json ``` フェンスが混ざっても最低限復元できるようにする
    (文字列リテラル内の `{`/`}` は無視して深さを数える)。
    """

    stripped = _CODE_FENCE_RE.sub("", text.strip()).strip()
    start = stripped.find("{")
    if start < 0:
        return stripped
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    return stripped[start:]


def _merge_score_adjustments(
    existing: list[ScoreAdjustment], new: list[ScoreAdjustment]
) -> list[ScoreAdjustment]:
    """同じ `spot_id` は後勝ちで上書きし、それ以外は両方残す(順序は維持)。"""

    merged: dict[str, ScoreAdjustment] = {value.spot_id: value for value in existing}
    for value in new:
        merged[value.spot_id] = value
    return list(merged.values())


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
