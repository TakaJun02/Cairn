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
    # 2026-08-06 レビュー是正(H-1 残穴、ADR-0024): `state.ask_timed_out` の
    # 実行時バックストップ。メイン・レコメンド SA・知識検索 SA それぞれの
    # 呼び出し元は guided schema から `ask_user` を外すことでタイムアウト後
    # の再質問を主に防ぐが、知識検索 SA は自身の内部ループの `ask_callback`
    # 可否をターン開始時に固定するため、同一 `search_knowledge` 呼び出し内で
    # タイムアウト後に再度 `ask_user` が選ばれる余地が残っていた。
    # `execute_ask_user` は 3 経路すべてが必ず通るチョークポイントなので、
    # ここで防ぐのがスキーマ除外(主防御)を経由しない経路にも効く唯一の
    # 場所である(§7 の追記どおり)。
    if state.ask_timed_out:
        return AskExecutionOutcome(
            executed=False,
            digest=(
                "このターンでは質問がタイムアウト済みのため、これ以上質問"
                "できません。最も確からしい解釈を採り、仮定を明示して"
                "進めてください。"
            ),
            guard_rule="H1",
        )

    guard = evaluate_ask_user(
        question,
        ask_user_count=state.ask_user_count,
        asked_slots=state.asked_slots,
        resolved_ambiguities=state.resolved_ambiguities,
        allowed_spot_ids=allowed_spot_ids,
        existing_spot_ids=existing_spot_ids,
        profile=state.profile,
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

    # 2026-08-04 レビュー是正(High・裁定14): `ask_user` も `step_results`
    # へ登録する。以前は登録されず、assistant `meta.tools`/`mode`
    # (data_model.md §4.4)に `ask_user` が一切反映されなかった。
    # レコメンド SA・知識検索 SA の内部呼び出しは、同じ `step_id` を使う
    # 自身の Tool 呼び出し(recommend/search_knowledge)が後で上書きするため
    # 安全(その手の観測としては recommend/search_knowledge が正)。
    state.step_results[step_id] = result

    answer_text = str(result.data.get("answer", ""))
    answered_by = str(result.data.get("answered_by", "free_text"))

    # R4 のカウンタ・A1(質問済み slot)は「質問を実際に提示した」ことに
    # 対して回す。timeout でも聞いたこと自体は変わらないので増やす
    # (増やさないと同じ slot をタイムアウトのたびに聞き直せてしまう)。
    state.ask_user_count += 1
    if question.kind == "preference" and question.slot is not None:
        slot_value = question.slot.value
        if slot_value not in state.asked_slots:
            state.asked_slots = [*state.asked_slots, slot_value]

    if answered_by == "timeout":
        # 2026-08-04 レビュー是正(Medium・裁定12): タイムアウトは「未回答」
        # であり、架空のユーザー発話ではない。
        # (a) user 行を書かない(`state.qa_answers` へ追加しない)
        # (b) `resolved_ambiguities` に登録しない(A5 の将来の聞き直しを
        #     不当に禁止しない)
        # (c) `update_profile` を回さない(回答が無いのに再実行しない)
        # (d) 軌跡には「未回答(タイムアウト)。仮定して進めよ」を返す
        # (e) 2026-08-06 レビュー是正(H-1、ADR-0024): `state.ask_timed_out`
        #     を立てる。これが無いと呼び出し元(SA)はタイムアウトを観測
        #     できず(executed=True・answer_text=None でプロンプトが変わら
        #     ない)、同じ質問を選び続けて最大 R4 回 × 10 分ブロックしうる。
        state.ask_timed_out = True
        return AskExecutionOutcome(
            executed=True,
            digest=(
                f"質問「{question.reason}」は未回答でした(タイムアウト)。"
                "最も確からしい解釈を採り、仮定を明示して進めてください。"
            ),
            answer_text=None,
            answered_by=answered_by,
        )

    resolved_spot_id: str | None = None
    if question.kind == "clarify":
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

    # 2026-08-04 レビュー是正(High・裁定13): 再実行は
    # `kind=="preference"` かつ(既に timeout は上で return 済みなので)
    # 回答が実在するときだけ行う。main の preference も対象。clarify は
    # 回さない(曖昧参照の選択を恒久的選好と誤認しない)。再実行プロンプト
    # には質問文 + 回答を渡す(自由記述の dates/origin 等が
    # `ProfileDelta` に無いフィールドでも失われないように)。
    if run_update_profile and question.kind == "preference":
        await update_profile(
            state,
            client=client,
            event_sink=event_sink,
            utterance_override=_qa_utterance(question, answer_text),
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


def _qa_utterance(question: AskUserArgs, answer_text: str) -> str:
    """`update_profile` の再実行に渡す発話。質問文 + 回答の両方を含める。"""

    return f"(質問: {question.reason}) {answer_text}"


__all__ = ["AskExecutionOutcome", "execute_ask_user"]
