"""understand / respond が共有する会話履歴の 4 層ビルダー。

`Docs/30_design/agent_react_architecture.md` §8・`Docs/30_design/data_model.md` §6
が正である構成:

    会話履歴 =
      ① threads.history_summary        … summarized_until_message_id までの LLM 要約
      ② 機械要約の列（あれば）          … ①より後〜直近2ターンより前の未畳み込みターン
      ③ 候補提示リストの機械要約        … 直近3リストまで。序数照応の担保
      ④ 直近2ターンの生テキスト        … user も assistant も content そのまま

予算超過時は②③の古い方から落とす（①は生成時に上限を持つため、④は指示語解決に
必須なため、どちらもここでは落とさない）。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_HISTORY_BUDGET_TOKENS = 1_700
DEFAULT_RAW_TURN_COUNT = 2
DEFAULT_CANDIDATE_LIST_LIMIT = 3


class HistoryMessage(Protocol):
    id: int
    role: str
    content: str
    meta: Mapping[str, Any]


TokenCounter = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class HistoryBuildResult:
    text: str
    estimated_tokens: int
    raw_turns: int
    summarized_turns: int
    candidate_lists: int
    dropped_sections: int
    mentioned_spot_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Turn:
    index: int
    messages: tuple[HistoryMessage, ...]

    @property
    def last_message_id(self) -> int:
        return self.messages[-1].id


def estimate_tokens(text: str) -> int:
    """追加 tokenizer を持たず、日英混在を安全側に見積もる。"""

    if not text:
        return 0
    ascii_count = sum(character.isascii() for character in text)
    non_ascii_count = len(text) - ascii_count
    return max(1, math.ceil(ascii_count / 4) + non_ascii_count)


def build_conversation_history(
    messages: Sequence[HistoryMessage],
    *,
    history_summary: str = "",
    summarized_until_message_id: int | None = None,
    max_tokens: int = DEFAULT_HISTORY_BUDGET_TOKENS,
    raw_turn_count: int = DEFAULT_RAW_TURN_COUNT,
    candidate_list_limit: int = DEFAULT_CANDIDATE_LIST_LIMIT,
    token_counter: TokenCounter = estimate_tokens,
) -> HistoryBuildResult:
    """① 要約 + ② 機械要約 + ③ 候補リスト + ④ 直近生テキストを組み立てる。

    ①(`history_summary`)と④(直近 `raw_turn_count` ターン)は常に残す。
    予算超過時は②③をまとめて古いターンから落とす。
    """

    turns = group_turns(messages)
    raw_start = max(0, len(turns) - raw_turn_count)
    fold_start = first_unfolded_turn_index(turns, summarized_until_message_id)

    layer2_items = [
        (turn.index, line)
        for turn in turns[fold_start:raw_start]
        if (line := _turn_summary_line(turn))
    ]
    candidate_items = _collect_candidate_lines(turns)[-candidate_list_limit:]
    raw_lines = [line for turn in turns[raw_start:] for line in _raw_lines(turn)]

    dropped = 0
    while (layer2_items or candidate_items) and token_counter(
        _assemble(history_summary, layer2_items, candidate_items, raw_lines)
    ) > max_tokens:
        oldest_layer2 = layer2_items[0][0] if layer2_items else None
        oldest_candidate = candidate_items[0][0] if candidate_items else None
        if oldest_candidate is None or (
            oldest_layer2 is not None and oldest_layer2 <= oldest_candidate
        ):
            layer2_items.pop(0)
        else:
            candidate_items.pop(0)
        dropped += 1

    text = _assemble(history_summary, layer2_items, candidate_items, raw_lines)
    mentioned = tuple(
        dict.fromkeys(
            [
                *_meta_spot_ids_for_turns(turns, (turn.index for turn in turns[raw_start:])),
                *_meta_spot_ids_for_turns(turns, (index for index, _ in layer2_items)),
                *_meta_spot_ids_for_turns(turns, (index for index, _ in candidate_items)),
            ]
        )
    )
    return HistoryBuildResult(
        text=text,
        estimated_tokens=token_counter(text),
        raw_turns=len(turns) - raw_start if turns else 0,
        summarized_turns=len(layer2_items),
        candidate_lists=len(candidate_items),
        dropped_sections=dropped,
        mentioned_spot_ids=mentioned,
    )


def summarize_assistant_event(meta: Mapping[str, Any]) -> str:
    """`messages.meta` だけから古い assistant 行を機械的に 1 行化する。"""

    summaries: list[str] = []
    candidate_line = _candidate_line(meta)
    if candidate_line:
        summaries.append(candidate_line)

    qa_name = meta.get("qa_spot_name") or meta.get("qa_spot_id")
    if isinstance(qa_name, str) and qa_name:
        summaries.append(f"[QA回答: {qa_name}]")

    itinerary_version = meta.get("itinerary_version")
    itinerary_names = _string_list(meta.get("itinerary_spot_names"))
    if isinstance(itinerary_version, int):
        suffix = f": {' / '.join(itinerary_names)}" if itinerary_names else ""
        summaries.append(f"[旅程更新 v{itinerary_version}{suffix}]")

    if summaries:
        return " ".join(summaries)

    tools = _string_list(meta.get("tools"))
    if tools:
        return f"[応答: {' → '.join(tools)}]"
    return "[応答]"


def group_turns(messages: Sequence[HistoryMessage]) -> list[Turn]:
    """user 発話を境に会話をターンへまとめる（history_summary.py とも共有）。"""

    turns: list[list[HistoryMessage]] = []
    for message in messages:
        if message.role == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return [Turn(index=index, messages=tuple(turn)) for index, turn in enumerate(turns)]


def first_unfolded_turn_index(
    turns: Sequence[Turn], summarized_until_message_id: int | None
) -> int:
    """`summarized_until_message_id` より後、まだ①に畳み込まれていない最初のターン。"""

    if summarized_until_message_id is None:
        return 0
    for turn in turns:
        if turn.last_message_id > summarized_until_message_id:
            return turn.index
    return len(turns)


def _turn_summary_line(turn: Turn) -> str:
    lines: list[str] = []
    for message in turn.messages:
        prefix = "u" if message.role == "user" else "a"
        body = (
            summarize_assistant_event(message.meta)
            if message.role == "assistant"
            else message.content.strip()
        )
        if body:
            lines.append(f"{prefix}: {body}")
    return "\n".join(lines)


def _raw_lines(turn: Turn) -> list[str]:
    lines: list[str] = []
    for message in turn.messages:
        prefix = "u" if message.role == "user" else "a"
        body = message.content.strip()
        if body:
            lines.append(f"{prefix}: {body}")
    return lines


def _collect_candidate_lines(turns: Sequence[Turn]) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    for turn in turns:
        for message in turn.messages:
            if message.role != "assistant":
                continue
            line = _candidate_line(message.meta)
            if line:
                items.append((turn.index, line))
    return items


def _candidate_line(meta: Mapping[str, Any]) -> str | None:
    candidate_names = _string_list(meta.get("candidate_names"))
    candidate_ids = _string_list(meta.get("candidate_spot_ids"))
    candidates = candidate_names or candidate_ids
    if not candidates:
        return None
    return f"[推薦{len(candidates)}件: {' / '.join(candidates)}]"


def _assemble(
    history_summary: str,
    layer2_items: Sequence[tuple[int, str]],
    candidate_items: Sequence[tuple[int, str]],
    raw_lines: Sequence[str],
) -> str:
    sections: list[str] = []
    if history_summary and history_summary.strip():
        sections.append("これまでの要約:\n" + history_summary.strip())
    if layer2_items:
        sections.append(
            "その後の出来事（要約未反映）:\n"
            + "\n".join(line for _, line in layer2_items)
        )
    if candidate_items:
        sections.append(
            "これまでに提示した候補:\n" + "\n".join(line for _, line in candidate_items)
        )
    if raw_lines:
        sections.append("直近のやり取り:\n" + "\n".join(raw_lines))
    return "\n\n".join(sections)


def _meta_spot_ids_for_turns(turns: Sequence[Turn], indices: Any) -> list[str]:
    index_set = set(indices)
    if not index_set:
        return []
    values: list[str] = []
    for turn in turns:
        if turn.index not in index_set:
            continue
        for message in turn.messages:
            values.extend(_spot_ids_from_meta(message.meta))
    return values


def _spot_ids_from_meta(meta: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "candidate_spot_ids",
        "itinerary_spot_ids",
        "mentioned_spot_ids",
    ):
        values.extend(_string_list(meta.get(key)))
    qa_spot_id = meta.get("qa_spot_id")
    if isinstance(qa_spot_id, str):
        values.append(qa_spot_id)
    return list(dict.fromkeys(values))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
