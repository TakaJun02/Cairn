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

from app.domains.conversation.itinerary_digest import UNNAMED_SPOT_JA, mask_spot_ids

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

    qa_spot_name = meta.get("qa_spot_name")
    qa_spot_id = meta.get("qa_spot_id")
    if isinstance(qa_spot_name, str) and qa_spot_name:
        summaries.append(f"[QA回答: {qa_spot_name}]")
    elif isinstance(qa_spot_id, str) and qa_spot_id:
        # 2026-08-04([25 §1-7] レビュー是正): name が無い/空でも spot_id
        # へフォールバックしない。中立表記(itinerary_digest.UNNAMED_SPOT_JA)
        # を使う。
        summaries.append(f"[QA回答: {UNNAMED_SPOT_JA}]")

    itinerary_version = meta.get("itinerary_version")
    itinerary_names = _string_list(meta.get("itinerary_spot_names"))
    if isinstance(itinerary_version, int):
        suffix = f": {' / '.join(itinerary_names)}" if itinerary_names else ""
        summaries.append(f"[旅程更新 v{itinerary_version}{suffix}]")

    if summaries:
        text = " ".join(summaries)
    else:
        tools = _string_list(meta.get("tools"))
        text = f"[応答: {' → '.join(tools)}]" if tools else "[応答]"
    # 2026-08-04([25 §1-7] レビュー是正・防御2層目): 永続化済みの旧形式
    # meta(`qa_spot_name`/`candidate_names`/`itinerary_spot_names` 等)に
    # spot_id がそのまま入っている場合の防御として、LLM コンテキストへ
    # 載せる直前でもう一度マスクする。`meta` 自体は書き換えない。
    return mask_spot_ids(text, {})


def group_turns(messages: Sequence[HistoryMessage]) -> list[Turn]:
    """`meta.turn_id` を境に会話をターンへまとめる（history_summary.py とも共有）。

    2026-08-04 レビュー是正(High・裁定11): `ask_user` への回答(§7)は同じ
    ターン内で複数の user 行として persist される
    (`repository.py:persist_turn`。元発話 + 各回答 + assistant 行が同一
    `turn_id` を共有する)。旧実装は「user 発話ごとに新しいターン」という
    ヒューリスティックだけを使っており、質問を挟んだターンを 3 ターンに
    分割し、④(直近2ターン生)から元発話が脱落しうった。

    `turn_id` を持つ行はそれを正として同一ターンにまとめる。`turn_id` が
    無い(移行前の)行は、従来のヒューリスティック(user 行ごとに新ターン)
    にフォールバックする。
    """

    turns: list[list[HistoryMessage]] = []
    current_turn_id: str | None = None
    for message in messages:
        message_turn_id = _turn_id(message)
        if message_turn_id is not None:
            if not turns or current_turn_id != message_turn_id:
                turns.append([message])
                current_turn_id = message_turn_id
            else:
                turns[-1].append(message)
            continue
        # turn_id が無い旧行: 従来のヒューリスティックへフォールバックする。
        current_turn_id = None
        if message.role == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return [Turn(index=index, messages=tuple(turn)) for index, turn in enumerate(turns)]


def _turn_id(message: HistoryMessage) -> str | None:
    value = message.meta.get("turn_id")
    return value if isinstance(value, str) and value else None


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
        line = format_raw_message_line(message)
        if line:
            lines.append(line)
    return lines


def format_raw_message_line(message: HistoryMessage) -> str | None:
    """1 メッセージを生層(④)向けの1行に整形する。

    2026-08-04 レビュー是正(High・裁定11): `ask_user` への回答行
    (`meta.answer_to` を持つ user 行。§7・data_model.md §4.4)は、回答
    だけでなく質問文も出す(`[質問: ...] → 回答: ...`)。回答単独では
    「何を聞かれて何と答えたか」が生層からも要約入力からも読めず、
    LLM が指示語・文脈を解けない(history_summary.py の fold_text も
    本関数を共有する)。
    """

    body = message.content.strip()
    if not body:
        return None
    if message.role == "user":
        question = _answer_to_question_text(message.meta)
        if question:
            return f"u: [質問: {question}] → 回答: {body}"
        return f"u: {body}"
    return f"a: {body}"


def _answer_to_question_text(meta: Mapping[str, Any]) -> str | None:
    answer_to = meta.get("answer_to")
    if isinstance(answer_to, Mapping):
        reason = answer_to.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return None


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
    names, ids = _presented_names_and_ids(meta)
    candidates = names or ids
    if not candidates:
        return None
    line = f"[推薦{len(candidates)}件: {' / '.join(candidates)}]"
    # 2026-08-04([25 §1-7] レビュー是正・防御2層目): `names` が空で `ids`
    # (生の spot_id)を直接使う経路、および旧形式で `names` 自体に spot_id
    # が入っている経路の両方に対する防御。この行は③候補提示リストの
    # 機械要約として LLM コンテキストへ直接載る(`_collect_candidate_lines`
    # 経由)ため、ここでマスクする。
    return mask_spot_ids(line, {})


def _presented_names_and_ids(meta: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """`meta.presented`(data_model.md §4.4。裁定14)から順序つき名前/id を取る。

    `presented` が無い(移行前の)行は、旧フィールド
    `candidate_names`/`candidate_spot_ids` にフォールバックする。
    """

    presented = meta.get("presented")
    if isinstance(presented, list) and presented:
        names: list[str] = []
        ids: list[str] = []
        for item in presented:
            if not isinstance(item, Mapping):
                continue
            spot_id = item.get("spot_id")
            name = item.get("name_ja")
            if isinstance(spot_id, str):
                ids.append(spot_id)
            if isinstance(name, str):
                names.append(name)
        if names or ids:
            return names, ids
    return _string_list(meta.get("candidate_names")), _string_list(
        meta.get("candidate_spot_ids")
    )


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
    _, presented_ids = _presented_names_and_ids(meta)
    values: list[str] = list(presented_ids)
    for key in ("itinerary_spot_ids", "mentioned_spot_ids"):
        values.extend(_string_list(meta.get(key)))
    qa_spot_id = meta.get("qa_spot_id")
    if isinstance(qa_spot_id, str):
        values.append(qa_spot_id)
    return list(dict.fromkeys(values))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
