"""⑤ `persist` 内・`done` 送出後に走る、会話履歴要約の畳み込み更新。

`Docs/30_design/agent_react_architecture.md` §8 の決定を実装する: 生層
（直近2ターン）から押し出されたターンを既存要約(`threads.history_summary`)へ
LLM 1 回で畳み込み、`summarized_until_message_id` を進める。本体トランザクション
とは別の小さな更新であり、**応答のクリティカルパスに載せない**。

失敗しても例外を上げずログのみで続行する（NFR-5）。この場合
`summarized_until_message_id` は進まず、押し出されたターンは②の機械要約として
履歴に残り続ける（縮退が自然に成立する設計）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from app.core.llm import GenerationClient
from app.domains.conversation.history import (
    DEFAULT_RAW_TURN_COUNT,
    first_unfolded_turn_index,
    format_raw_message_line,
    group_turns,
)
from app.domains.conversation.state import MessageState, TurnState

logger = logging.getLogger("app.conversation.history_summary")

HISTORY_SUMMARY_EXPECTED_TOKENS = 600
HISTORY_SUMMARY_MAX_TOKENS = int(HISTORY_SUMMARY_EXPECTED_TOKENS * 1.5)
HISTORY_SUMMARY_WALLCLOCK_SEC = 30.0

HISTORY_SUMMARY_SYSTEM_PROMPT = """あなたは鳥海山観光ガイダンスの会話ログを
要約する係です。既存の要約と、その後に交わされた会話のターンを、
1 つの新しい要約に書き直してください。

必須規則:
- 決まった事実（選んだ POI・確定した日程・約束・有効な条件）は絶対に落としません。
- 分量は日本語で概ね 600 トークン以内に収めます。
- 説明文・見出し・Markdown は付けず、要約本文だけを出力します。
"""


@dataclass(frozen=True, slots=True)
class HistorySummaryState:
    """要約更新に必要な、永続化済みの現在状態。"""

    history_summary: str
    summarized_until_message_id: int | None
    messages: list[MessageState]


class HistorySummaryRepositoryPort(Protocol):
    async def load_history_summary_state(self, thread_id: int) -> HistorySummaryState: ...

    async def commit_history_summary(
        self,
        thread_id: int,
        *,
        summary: str,
        summarized_until_message_id: int,
    ) -> None: ...


async def update_history_summary(
    state: TurnState,
    *,
    repository: HistorySummaryRepositoryPort,
    client: GenerationClient | None = None,
    raw_turn_count: int = DEFAULT_RAW_TURN_COUNT,
) -> None:
    """押し出されたターンを既存要約へ畳み込む。失敗しても例外を上げない。"""

    try:
        await _update_history_summary(
            state,
            repository=repository,
            client=client,
            raw_turn_count=raw_turn_count,
        )
    except Exception:  # noqa: BLE001 - 要約失敗で対話を止めない（NFR-5）
        logger.exception(
            "history_summary_update_failed",
            extra={"thread_id": state.thread_id, "turn_id": state.turn_id},
        )


async def _update_history_summary(
    state: TurnState,
    *,
    repository: HistorySummaryRepositoryPort,
    client: GenerationClient | None,
    raw_turn_count: int,
) -> None:
    summary_state = await repository.load_history_summary_state(state.thread_id)
    turns = group_turns(summary_state.messages)
    raw_start = max(0, len(turns) - raw_turn_count)
    fold_start = first_unfolded_turn_index(
        turns, summary_state.summarized_until_message_id
    )
    foldable_turns = turns[fold_start:raw_start]
    if not foldable_turns:
        return

    # 2026-08-04 レビュー是正(High・裁定11): 生層(history.py)と同じ整形器
    # を使い、`ask_user` への回答行にも質問文を含める(要約が「回答だけ」を
    # 畳み込んで質問の文脈を失わないように)。
    fold_text = "\n".join(
        line
        for turn in foldable_turns
        for message in turn.messages
        if (line := format_raw_message_line(message))
    )
    if not fold_text:
        return

    resolved_client = client or GenerationClient()
    messages = [
        {"role": "system", "content": HISTORY_SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "既存の要約:\n"
                + (summary_state.history_summary.strip() or "(なし)")
                + "\n\n新しく畳み込むターン:\n"
                + fold_text
            ),
        },
    ]
    new_summary = await resolved_client.generate(
        messages,
        temperature=0.0,
        max_tokens=HISTORY_SUMMARY_MAX_TOKENS,
    )
    new_summary = new_summary.strip()
    if not new_summary:
        return

    last_message_id = foldable_turns[-1].last_message_id
    await repository.commit_history_summary(
        state.thread_id,
        summary=new_summary,
        summarized_until_message_id=last_message_id,
    )
