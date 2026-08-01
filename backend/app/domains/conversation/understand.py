"""N2 `understand`: 1 回の guided JSON と、JSON 不正時だけの 1 回再試行。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import ValidationError

from app.core.llm import GenerationClient, GenerationError
from app.domains.conversation.events import EventSinkLike, emit, state_event
from app.domains.conversation.guards import (
    validate_and_normalize_constraints,
    validate_clarification,
    validate_classification_completeness,
    validate_reference_closed_world,
)
from app.domains.conversation.prompts import (
    build_understand_messages,
    understand_guided_schema,
)
from app.domains.conversation.state import DegradedState, RejectedStep, TurnState
from app.domains.conversation.types import (
    Intent,
    ReferenceResolution,
    UnderstandAction,
    UnderstandOutput,
    UnmodeledItem,
)

UNDERSTAND_EXPECTED_TOKENS = 800
UNDERSTAND_MAX_TOKENS = int(UNDERSTAND_EXPECTED_TOKENS * 1.5)
UNDERSTAND_WALLCLOCK_SEC = 90.0
UNDERSTAND_MAX_ATTEMPTS = 2


class GenerationPort(Protocol):
    async def generate(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


class UnderstandFatalError(RuntimeError):
    """再試行を含めても N2 の契約を満たせなかった。"""


class RepetitionDetectedError(ValueError):
    """同一 n-gram の連続生成を検知した。"""


async def understand(
    state: TurnState,
    *,
    client: GenerationPort | None = None,
    event_sink: EventSinkLike = None,
    wallclock_sec: float = UNDERSTAND_WALLCLOCK_SEC,
) -> TurnState:
    """TurnState の N2 欄だけを埋める。"""

    resolved_client = client or GenerationClient()
    active_constraints = (
        state.itinerary.constraints
        if state.itinerary is not None
        else state.pending_constraints
    )
    constraint_ids = [
        str(value["id"])
        for value in active_constraints
        if isinstance(value.get("id"), str) and value["id"]
    ]
    schema = understand_guided_schema(
        state.spot_id_vocab,
        constraint_ids,
        # G8 は生成後に plan を復元できないため、連続時は
        # guided decoding の時点で done に絞り、同じ 1 回で plan を出させる。
        allow_clarification=state.clarify_streak < 1,
    )
    messages = build_understand_messages(state)
    failures: list[str] = []
    output: UnderstandOutput | None = None
    started_at = time.perf_counter()
    for attempt in range(UNDERSTAND_MAX_ATTEMPTS):
        state.understand_attempts = attempt + 1
        attempt_messages = _retry_messages(messages, failures[-1] if failures else None)
        try:
            async with asyncio.timeout(wallclock_sec):
                raw = await resolved_client.generate(
                    attempt_messages,
                    temperature=0.0,
                    max_tokens=UNDERSTAND_MAX_TOKENS,
                    extra_body={
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "conversation_understand",
                                "strict": True,
                                "schema": schema,
                            },
                        }
                    },
                )
            if has_repeated_ngram(raw):
                raise RepetitionDetectedError("同一 n-gram の反復を検知しました")
            output = UnderstandOutput.model_validate(json.loads(raw))
            break
        except (json.JSONDecodeError, ValidationError, RepetitionDetectedError) as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            if attempt + 1 >= UNDERSTAND_MAX_ATTEMPTS:
                break
        except (GenerationError, TimeoutError) as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            # 通信・ウォールクロック失敗には論理再試行を足さない。
            break

    state.log_fields["understand_ms"] = round(
        (time.perf_counter() - started_at) * 1000,
        2,
    )
    state.understand_failures = failures
    if output is None:
        state.understand_failed = True
        state.log_fields["understand_action"] = None
        raise UnderstandFatalError("understand の guided JSON を確定できませんでした")
    if state.understand_attempts > 1:
        state.degraded.append(
            DegradedState(
                code="understand_retry",
                stage="understand",
                message="guided JSON を 1 回再試行しました",
            )
        )

    normalized_output = _deduplicate_output(output)
    _copy_output(state, normalized_output)
    _postvalidate_extractions(state)
    await _apply_clarification_guard(state, event_sink)
    state.log_fields["understand_action"] = (
        state.understand_action.value if state.understand_action is not None else None
    )
    state.log_fields["intent"] = state.intent.value if state.intent is not None else None
    return state


def has_repeated_ngram(
    text: str,
    *,
    ngram_size: int = 8,
    repeat_threshold: int = 6,
) -> bool:
    """連続する同一 token n-gram と文字列ブロックの双方を検知する。"""

    tokens = re.findall(r"[\w一-龥ぁ-んァ-ヶー]+|[^\w\s]", text)
    if len(tokens) >= ngram_size * repeat_threshold:
        for start in range(len(tokens) - ngram_size * repeat_threshold + 1):
            block = tokens[start : start + ngram_size]
            if all(
                tokens[start + offset * ngram_size : start + (offset + 1) * ngram_size]
                == block
                for offset in range(1, repeat_threshold)
            ):
                return True
    # 空白のない日本語や JSON 断片も拾う。短い `{}` 等は誤検知しない。
    return re.search(r"(.{8,128}?)\1{5,}", text, flags=re.DOTALL) is not None


def _retry_messages(
    messages: list[dict[str, str]], failure: str | None
) -> list[dict[str, str]]:
    if failure is None:
        return [dict(value) for value in messages]
    result = [dict(value) for value in messages]
    result[0]["content"] += (
        "\n再試行です。前回は契約違反でした。全必須フィールドと "
        "action/plan/clarify の"
        f"整合を確認してください。原因: {failure[:240]}"
    )
    return result


def _copy_output(state: TurnState, output: UnderstandOutput) -> None:
    state.understand_action = output.action
    state.intent = output.intent
    state.plan = output.plan
    state.profile_delta = output.profile_delta
    state.constraints = output.constraints
    state.constraints_remove = output.constraints_remove
    state.score_adjustments = output.score_adjustments
    state.selection_hints = output.selection_hints
    state.unmodeled = output.unmodeled
    state.references = output.references
    state.clarification = output.clarify


def _deduplicate_output(output: UnderstandOutput) -> UnderstandOutput:
    """xgrammar に `uniqueItems` を渡さず、同等の処理をコードで行う。"""

    references = _unique_by(
        output.references,
        lambda value: (value.surface, value.spot_id),
    )
    constraints = _unique_by(
        output.constraints,
        lambda value: (
            value.pred,
            json.dumps(value.args, ensure_ascii=False, sort_keys=True, default=str),
            value.source_text,
        ),
    )
    constraints_remove = list(dict.fromkeys(output.constraints_remove))
    score_adjustments = _unique_by(
        output.score_adjustments,
        lambda value: value.spot_id,
    )
    selection_hints = _unique_by(output.selection_hints, lambda value: value.text)
    unmodeled = _unique_by(output.unmodeled, lambda value: value.text)
    clarification = output.clarify
    if clarification is not None:
        clarification = clarification.model_copy(
            update={
                "options": _unique_by(
                    clarification.options,
                    lambda value: (
                        value.resolves_to.kind,
                        value.resolves_to.value,
                    ),
                )
            },
            deep=True,
        )
    return output.model_copy(
        update={
            "references": references,
            "constraints": constraints,
            "constraints_remove": constraints_remove,
            "score_adjustments": score_adjustments,
            "selection_hints": selection_hints,
            "unmodeled": unmodeled,
            "clarify": clarification,
        },
        deep=True,
    )


def _postvalidate_extractions(state: TurnState) -> None:
    allowed = set(state.spot_id_vocab)
    existing = set(state.spot_catalog)
    valid_references: list[ReferenceResolution] = []
    for reference in state.references:
        checked = validate_reference_closed_world(
            reference,
            allowed_spot_ids=allowed,
            existing_spot_ids=existing,
        )
        if checked.accepted:
            valid_references.append(reference)
        else:
            state.rejected_steps.append(
                RejectedStep(
                    step_id=None,
                    tool=None,
                    rule=checked.rule or "closed_world",
                    reason=checked.reason or "照応を検証できませんでした",
                    step={"reference": reference.model_dump(mode="json")},
                )
            )
    state.references = valid_references

    valid_adjustments = []
    for adjustment in state.score_adjustments:
        if adjustment.spot_id in allowed and adjustment.spot_id in existing:
            valid_adjustments.append(adjustment)
        else:
            state.unmodeled.append(
                UnmodeledItem(
                    text=adjustment.why or adjustment.spot_id,
                    reason=f"参照できない spot_id です: {adjustment.spot_id}",
                )
            )
    state.score_adjustments = valid_adjustments

    active_ids = {
        str(value.get("id"))
        for value in (
            state.itinerary.constraints
            if state.itinerary is not None
            else state.pending_constraints
        )
        if value.get("id") is not None
    }
    valid_remove = [value for value in state.constraints_remove if value in active_ids]
    for value in state.constraints_remove:
        if value not in active_ids:
            state.unmodeled.append(
                UnmodeledItem(
                    text=f"制約 {value} を取り消す",
                    reason="現在有効な制約 id ではありません",
                )
            )
    state.constraints_remove = valid_remove

    version = state.itinerary.version + 1 if state.itinerary is not None else 1
    constraint_result = validate_and_normalize_constraints(
        state.constraints,
        state.spot_catalog,
        created_at_version=version,
        used_ids=active_ids,
    )
    state.constraints = list(constraint_result.constraints)
    state.unmodeled.extend(constraint_result.unmodeled)

    # handling Literal と数え上げを組み合わせ、4 経路外を許さない。
    count = (
        len(state.constraints)
        + len(state.score_adjustments)
        + len(state.selection_hints)
        + len(state.unmodeled)
    )
    completeness = validate_classification_completeness(
        extracted_count=count,
        constraints=state.constraints,
        score_adjustments=state.score_adjustments,
        selection_hints=state.selection_hints,
        unmodeled=state.unmodeled,
    )
    if not completeness.accepted:  # pragma: no cover - Literal 型が防ぐ最後の防御
        state.unmodeled.append(
            UnmodeledItem(
                text="分類できなかった要望",
                reason=completeness.reason,
            )
        )


async def _apply_clarification_guard(
    state: TurnState,
    event_sink: EventSinkLike,
) -> None:
    if state.understand_action is not UnderstandAction.ASK_USER:
        return
    if state.clarification is None:  # Pydantic が防ぐ
        state.understand_action = UnderstandAction.DONE
        return
    decision = validate_clarification(
        state.clarification,
        allowed_spot_ids=set(state.spot_id_vocab),
        existing_spot_ids=set(state.spot_catalog),
        resolved_ambiguities=state.resolved_ambiguities,
        clarify_streak=state.clarify_streak,
        # action=ask_user は schema 上 plan=[] なので、G9 は
        # intent=unclear かどうかも見ないと実行時に決して発火しない。
        has_viable_plan=(
            bool(state.plan)
            or state.intent not in {None, Intent.UNCLEAR}
        ),
    )
    if not decision.accepted:
        state.rejected_steps.append(
            RejectedStep(
                step_id=None,
                tool=None,
                rule=decision.rule or "G6-G9",
                reason=decision.reason or "聞き返しをガードが破棄しました",
                step={"clarify": state.clarification.model_dump(mode="json")},
            )
        )
        # G8 だけ最初の具体値を仮定する。G6 の不正値や
        # G7/G9 で棄却した聞き返しから新たな照応を作らない。
        if decision.rule == "G8":
            first = (
                state.clarification.options[0]
                if state.clarification.options
                else None
            )
            if first is not None and first.resolves_to.kind == "spot_id":
                state.references.append(
                    ReferenceResolution(
                        surface=state.clarification.surface,
                        spot_id=first.resolves_to.value,
                    )
                )
            if first is not None:
                state.assumptions.append(
                    f"「{state.clarification.surface}」は {first.label} と仮定しました"
                )
        state.understand_action = UnderstandAction.DONE
        state.clarification = None
        return
    await emit(
        event_sink,
        state_event(
            "clarify",
            surface=state.clarification.surface,
            options=[
                {
                    "label": option.label,
                    "value": option.resolves_to.value,
                }
                for option in state.clarification.options
            ],
        ),
    )


def _unique_by(values: Sequence[Any], key: Any) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        marker = key(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result
