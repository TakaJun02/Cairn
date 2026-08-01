"""N5 `respond`: 共通テンプレート 1 本による日本語ストリーミング。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, runtime_checkable

from app.core.llm import GenerationClient
from app.domains.conversation.events import EventSinkLike, emit, error_event, token_event
from app.domains.conversation.guards import validate_response_spot_names
from app.domains.conversation.prompts import build_respond_messages
from app.domains.conversation.state import DegradedState, TurnState
from app.domains.conversation.types import (
    Intent,
    ResponseMode,
    ToolName,
    UnderstandAction,
)
from app.domains.conversation.understand import has_repeated_ngram

RESPOND_EXPECTED_TOKENS = 600
RESPOND_MAX_TOKENS = int(RESPOND_EXPECTED_TOKENS * 1.5)
RESPOND_WALLCLOCK_SEC = 90.0


@runtime_checkable
class StreamingGenerationPort(Protocol):
    def stream(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]: ...


class GenerateOnlyPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


class RespondGenerationError(RuntimeError):
    """N5 が完全な本文を生成できなかった。"""


async def respond(
    state: TurnState,
    *,
    client: StreamingGenerationPort | GenerateOnlyPort | None = None,
    event_sink: EventSinkLike = None,
    wallclock_sec: float = RESPOND_WALLCLOCK_SEC,
) -> TurnState:
    """生成済み断片を送出し、例外時も partial 本文を TurnState に残す。"""

    resolved_client = client or GenerationClient()
    mode = response_mode(state)
    messages = build_respond_messages(state, mode=mode)
    started_at = time.perf_counter()
    try:
        async with asyncio.timeout(wallclock_sec):
            async for chunk in _stream_or_generate(resolved_client, messages):
                if not chunk:
                    continue
                candidate = state.assistant_text + chunk
                if has_repeated_ngram(candidate):
                    raise RespondGenerationError("応答の反復 n-gram を検知しました")
                state.assistant_text = candidate
                await emit(event_sink, token_event(chunk))
        state.respond_status = "complete"
    except Exception as exc:
        state.respond_status = "partial" if state.assistant_text else "failed"
        state.log_fields["respond_ms"] = round(
            (time.perf_counter() - started_at) * 1000,
            2,
        )
        raise RespondGenerationError("応答の生成に失敗しました") from exc

    state.log_fields["respond_ms"] = round(
        (time.perf_counter() - started_at) * 1000,
        2,
    )
    state.log_fields["respond_mode"] = mode.value
    checked = validate_response_spot_names(
        state.assistant_text,
        all_spot_names=state.spot_names,
        allowed_spot_ids=_allowed_spot_ids(state),
    )
    if not checked.accepted:
        state.degraded.append(
            DegradedState(
                code="response_closed_world_violation",
                stage="respond",
                message=checked.reason or "応答のクローズドワールド違反",
            )
        )
        await emit(
            event_sink,
            error_event(
                stage="respond",
                code="response_closed_world_violation",
                degraded=True,
                message="応答に未確認の地点名が含まれました",
            ),
        )
    return state


def response_mode(state: TurnState) -> ResponseMode:
    if state.understand_action is UnderstandAction.ASK_USER and state.clarification is not None:
        return ResponseMode.CLARIFICATION
    if state.should_end_turn:
        return ResponseMode.QUESTION
    if state.understand_failed:
        return ResponseMode.FAILURE
    if state.aborted_at is not None and not state.step_results:
        return ResponseMode.FAILURE
    if state.accepted_steps and not state.step_results and state.skipped_steps:
        return ResponseMode.FAILURE
    if state.plan and not state.accepted_steps and state.rejected_steps:
        return ResponseMode.FAILURE
    if (
        state.intent in {Intent.RECOMMEND, Intent.PLAN, Intent.EDIT, Intent.QA}
        and not state.accepted_steps
        and not state.step_results
    ):
        return ResponseMode.FAILURE
    if state.intent is Intent.UNCLEAR and not state.step_results:
        return ResponseMode.FAILURE
    return ResponseMode.EXPLANATION


async def _stream_or_generate(
    client: StreamingGenerationPort | GenerateOnlyPort,
    messages: list[dict[str, str]],
) -> AsyncIterator[str]:
    if isinstance(client, StreamingGenerationPort):
        async for value in client.stream(
            messages,
            temperature=0.2,
            max_tokens=RESPOND_MAX_TOKENS,
        ):
            yield value
        return
    text = await client.generate(
        messages,
        temperature=0.2,
        max_tokens=RESPOND_MAX_TOKENS,
    )
    # generate しか持たないモックも同じイベント経路へ流す。
    yield text


def _allowed_spot_ids(state: TurnState) -> set[str]:
    values: set[str] = {reference.spot_id for reference in state.references}
    values.update(candidate.spot_id for candidate in state.last_candidates)
    for result in state.step_results.values():
        raw_ids = result.data.get("spot_ids")
        if isinstance(raw_ids, list):
            values.update(value for value in raw_ids if isinstance(value, str))
        spot_id = result.data.get("spot_id")
        if isinstance(spot_id, str):
            values.add(spot_id)
        diff = result.data.get("diff")
        if isinstance(diff, dict):
            for field in ("added", "removed", "retimed"):
                raw_ids = diff.get(field)
                if isinstance(raw_ids, list):
                    values.update(value for value in raw_ids if isinstance(value, str))
            moved = diff.get("moved")
            if isinstance(moved, list):
                values.update(
                    value["spot_id"]
                    for value in moved
                    if isinstance(value, dict) and isinstance(value.get("spot_id"), str)
                )
        concessions = result.data.get("concessions")
        if isinstance(concessions, list):
            values.update(
                _concession_spot_ids(
                    concessions,
                    known_spot_ids=set(state.spot_names),
                )
            )
        itinerary = result.data.get("itinerary")
        if isinstance(itinerary, dict):
            for day in itinerary.get("days", []):
                if isinstance(day, dict):
                    for endpoint_name in ("origin", "destination"):
                        endpoint = day.get(endpoint_name)
                        if isinstance(endpoint, dict) and isinstance(endpoint.get("spot_id"), str):
                            values.add(endpoint["spot_id"])
                    values.update(
                        item["spot_id"]
                        for item in day.get("items", [])
                        if isinstance(item, dict) and isinstance(item.get("spot_id"), str)
                    )
    # QA 以外でも現在旅程の説明は入力事実なので許可する。
    if state.itinerary is not None:
        for day in state.itinerary.itinerary.days:
            values.add(day.origin.spot_id)
            values.update(item.spot_id for item in day.items)
            values.add(day.destination.spot_id)
    # ask_user は地点を扱わないが、Tool 型の比較を明示する。
    values.update(
        result.data.get("spot_id")
        for result in state.step_results.values()
        if result.tool is ToolName.SEARCH_KNOWLEDGE and isinstance(result.data.get("spot_id"), str)
    )
    return values


def _concession_spot_ids(
    value: Any,
    *,
    known_spot_ids: set[str],
    field_name: str | None = None,
) -> set[str]:
    """concession の `args.target` 等に含まれる既知地点も拾う。"""

    if isinstance(value, str):
        is_spot_field = field_name == "spot_id" or field_name == "spot_ids"
        return {value} if is_spot_field or value in known_spot_ids else set()
    if isinstance(value, dict):
        result: set[str] = set()
        for key, child in value.items():
            result.update(
                _concession_spot_ids(
                    child,
                    known_spot_ids=known_spot_ids,
                    field_name=key,
                )
            )
        return result
    if isinstance(value, list):
        result = set()
        for child in value:
            result.update(
                _concession_spot_ids(
                    child,
                    known_spot_ids=known_spot_ids,
                    field_name=field_name,
                )
            )
        return result
    return set()
