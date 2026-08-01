"""`understand` と `respond` が共有する会話履歴の 3 層ビルダー。"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class HistoryMessage(Protocol):
    role: str
    content: str
    meta: Mapping[str, Any]


TokenCounter = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class HistoryBuildResult:
    text: str
    estimated_tokens: int
    raw_turns: int
    compressed_turns: int
    dropped_turns: int
    mentioned_spot_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Turn:
    messages: tuple[HistoryMessage, ...]


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
    max_tokens: int = 4_000,
    raw_turn_count: int = 3,
    token_counter: TokenCounter = estimate_tokens,
) -> HistoryBuildResult:
    """直近 3 ターンは生、それ以前は assistant だけイベント要約にする。

    予算超過時は圧縮層の古いターンから落とす。生層だけで超過する
    極端なケースでは、最古の生メッセージの先頭を省略して末尾を残す。
    """

    turns = _group_turns(messages)
    raw_start = max(0, len(turns) - raw_turn_count)
    rendered: list[tuple[int, str, tuple[str, ...]]] = []
    for index, turn in enumerate(turns):
        is_raw = index >= raw_start
        lines: list[str] = []
        ids: list[str] = []
        for message in turn.messages:
            ids.extend(_spot_ids_from_meta(message.meta))
            prefix = "u" if message.role == "user" else "a"
            if message.role == "assistant" and not is_raw:
                body = summarize_assistant_event(message.meta)
            else:
                body = message.content.strip()
            if body:
                lines.append(f"{prefix}: {body}")
        if lines:
            rendered.append((index, "\n".join(lines), tuple(dict.fromkeys(ids))))

    dropped = 0
    while rendered and _rendered_tokens(rendered, token_counter) > max_tokens:
        compressed_position = next(
            (position for position, item in enumerate(rendered) if item[0] < raw_start),
            None,
        )
        if compressed_position is None:
            break
        rendered.pop(compressed_position)
        dropped += 1

    if rendered and _rendered_tokens(rendered, token_counter) > max_tokens:
        rendered = _truncate_raw_history(rendered, max_tokens, token_counter)

    text = "\n".join(item[1] for item in rendered)
    mentioned = tuple(
        dict.fromkeys(spot_id for item in rendered for spot_id in item[2])
    )
    return HistoryBuildResult(
        text=text,
        estimated_tokens=token_counter(text),
        raw_turns=sum(item[0] >= raw_start for item in rendered),
        compressed_turns=sum(item[0] < raw_start for item in rendered),
        dropped_turns=dropped,
        mentioned_spot_ids=mentioned,
    )


def summarize_assistant_event(meta: Mapping[str, Any]) -> str:
    """`messages.meta` だけから古い assistant 行を機械的に 1 行化する。"""

    summaries: list[str] = []
    candidate_names = _string_list(meta.get("candidate_names"))
    candidate_ids = _string_list(meta.get("candidate_spot_ids"))
    candidates = candidate_names or candidate_ids
    if candidates:
        summaries.append(f"[推薦{len(candidates)}件: {' / '.join(candidates)}]")

    qa_name = meta.get("qa_spot_name") or meta.get("qa_spot_id")
    if isinstance(qa_name, str) and qa_name:
        summaries.append(f"[QA回答: {qa_name}]")

    itinerary_version = meta.get("itinerary_version")
    itinerary_names = _string_list(meta.get("itinerary_spot_names"))
    if isinstance(itinerary_version, int):
        suffix = f": {' / '.join(itinerary_names)}" if itinerary_names else ""
        summaries.append(f"[旅程更新 v{itinerary_version}{suffix}]")

    ask_slot = meta.get("ask_slot")
    if isinstance(ask_slot, str) and ask_slot:
        summaries.append(f"[選好質問: {ask_slot}]")

    clarify_surface = meta.get("clarify_surface")
    if isinstance(clarify_surface, str) and clarify_surface:
        summaries.append(f"[聞き返し: {clarify_surface}]")

    if summaries:
        return " ".join(summaries)

    tools = _string_list(meta.get("tools"))
    if tools:
        return f"[応答: {' → '.join(tools)}]"
    return "[応答]"


def _group_turns(messages: Sequence[HistoryMessage]) -> list[_Turn]:
    turns: list[list[HistoryMessage]] = []
    for message in messages:
        if message.role == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return [_Turn(messages=tuple(turn)) for turn in turns]


def _rendered_tokens(
    rendered: Sequence[tuple[int, str, tuple[str, ...]]],
    token_counter: TokenCounter,
) -> int:
    return token_counter("\n".join(item[1] for item in rendered))


def _truncate_raw_history(
    rendered: list[tuple[int, str, tuple[str, ...]]],
    max_tokens: int,
    token_counter: TokenCounter,
) -> list[tuple[int, str, tuple[str, ...]]]:
    result = list(rendered)
    while len(result) > 1 and _rendered_tokens(result, token_counter) > max_tokens:
        result.pop(0)
    if not result:
        return result
    index, body, ids = result[0]
    if _rendered_tokens(result, token_counter) <= max_tokens:
        return result
    # tokenizer 非依存で二分探索し、最古行の末尾を可能な限り残す。
    low, high = 0, len(body)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        candidate = "…" + body[len(body) - midpoint :]
        trial = [(index, candidate, ids), *result[1:]]
        if _rendered_tokens(trial, token_counter) <= max_tokens:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    result[0] = (index, best, ids)
    return result


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
