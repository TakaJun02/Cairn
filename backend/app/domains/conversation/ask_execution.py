"""`ask_user` の実行を 3 経路(メイン・レコメンド SA・知識検索 SA)で共通化する。

`Docs/30_design/agent_react_architecture.md` §7 の手順そのもの:
ガード検査(§10)→ `tools.ask_user`(HITL 待ち受け。`tool_adapters.ToolAdapters`
の実体)→ 回答をターン状態(`asked_slots`/`resolved_ambiguities`/
`ask_user_count`/`qa_answers`)へ反映 → 回答に対して `update_profile` を
1 回だけ再実行する(§2・§4)。

`main_agent.py` は自身の import グラフの都合上 `recommend_agent.py` を import
しているため、両者が共有するこのロジックは循環 import を避けるためどちらにも
属さない中立モジュールに置く。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.domains.conversation.events import EventSinkLike
from app.domains.conversation.guards import evaluate_ask_user
from app.domains.conversation.state import TurnState
from app.domains.conversation.tool_ports import ConversationToolPort
from app.domains.conversation.types import AskUserArgs, ToolError
from app.domains.conversation.update_profile import update_profile


class GenerationPort(Protocol):
    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        extra_body: dict[str, Any] | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class AskExecutionOutcome:
    """呼び出し元(main_agent/recommend_agent/narration アダプタ)が使う結果。

    `digest` はそのまま observation/action_log の文字列として使える
    (ガードで落ちたときも「聞けなかった理由 + 仮定して進めよ」を含む。
    23_ux_issues.md §7-3 の教訓: 手ごと消滅させない)。
    """

    executed: bool
    digest: str
    answer_text: str | None = None
    answered_by: str | None = None
    error: dict[str, Any] | None = None
    resolved_spot_id: str | None = None
    guard_rule: str | None = None


async def execute_ask_user(
    state: TurnState,
    tools: ConversationToolPort,
    question: AskUserArgs,
    *,
    step_id: int,
    client: GenerationPort,
    event_sink: EventSinkLike = None,
    allowed_spot_ids: set[str] | None = None,
    existing_spot_ids: set[str] | None = None,
    run_update_profile: bool = True,
) -> AskExecutionOutcome:
    guard = evaluate_ask_user(
        question,
        ask_user_count=state.ask_user_count,
        ask_streak=state.ask_streak,
        asked_slots=state.asked_slots,
        resolved_ambiguities=state.resolved_ambiguities,
        allowed_spot_ids=allowed_spot_ids,
        existing_spot_ids=existing_spot_ids,
    )
    if not guard.accepted:
        return AskExecutionOutcome(
            executed=False,
            digest=(
                f"質問できませんでした({guard.reason})。"
                "最も確からしい解釈を採り、仮定を明示して進めてください。"
            ),
            guard_rule=guard.rule,
        )

    result = await tools.ask_user(step_id=step_id, args=question)
    if isinstance(result, ToolError):
        return AskExecutionOutcome(
            executed=False,
            digest=result.message_ja,
            error={
                "code": result.code.value,
                "message_ja": result.message_ja,
                "recoverable": result.recoverable,
            },
        )

    answer_text = str(result.data.get("answer", ""))
    answered_by = str(result.data.get("answered_by", "free_text"))

    state.ask_user_count += 1
    resolved_spot_id: str | None = None
    if question.kind == "preference" and question.slot is not None:
        slot_value = question.slot.value
        if slot_value not in state.asked_slots:
            state.asked_slots = [*state.asked_slots, slot_value]
    elif question.kind == "clarify":
        resolved_option = next(
            (
                option
                for option in question.options
                if option.label == answer_text or option.value == answer_text
            ),
            None,
        )
        resolved_spot_id = resolved_option.value if resolved_option is not None else None
        state.resolved_ambiguities = [
            *state.resolved_ambiguities,
            {"surface": question.surface, "resolved_to": resolved_spot_id},
        ]

    state.qa_answers.append(
        {
            "answer": answer_text,
            "meta": {
                "answer_to": question.model_dump(mode="json", exclude_none=True),
                "answered_by": answered_by,
            },
        }
    )

    if run_update_profile:
        await update_profile(
            state,
            client=client,
            event_sink=event_sink,
            utterance_override=answer_text,
        )

    digest = (
        f"質問「{question.reason}」への回答: {answer_text}"
        f"(回答方法: {answered_by})"
    )
    return AskExecutionOutcome(
        executed=True,
        digest=digest,
        answer_text=answer_text,
        answered_by=answered_by,
        resolved_spot_id=resolved_spot_id,
    )


__all__ = ["AskExecutionOutcome", "execute_ask_user"]
