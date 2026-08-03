"""コードが強制するガード群(`Docs/30_design/agent_react_architecture.md` §10)。

- `validate_and_normalize_constraints`: メインエージェントが直接書く制約
  (述語 DSL 17 種)の検証。`plan_itinerary`/`edit_itinerary` アダプタが使う
- `normalize_revert_ops`: `edit_itinerary.ops` に `revert` が混じったときの
  排他化。メインループが Tool 実行前に使う
- `validate_response_spot_names`: `respond` のクローズドワールド検査
- `evaluate_ask_user`(R4・A1〜A5): `ask_user` の HITL 抑制ガード。
  `ask_execution.execute_ask_user` がメイン・レコメンド SA・知識検索 SA の
  3 経路共通で呼ぶ(§7・§10)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.domains.conversation.state import SpotFact
from app.domains.conversation.types import AskUserArgs, ConstraintDraft, UnmodeledItem
from app.domains.itinerary.predicates import normalize_constraints

# R4: `ask_user` は 1 ターン 2 回まで(メイン・SA 合算。§3.5・§10)。
MAX_ASK_USER_PER_TURN = 2
# A2: 質問を含むターンの連続は 2 ターンまで。
MAX_ASK_STREAK = 2


def has_repeated_ngram(
    text: str,
    *,
    ngram_size: int = 8,
    repeat_threshold: int = 6,
) -> bool:
    """連続する同一 token n-gram と文字列ブロックの双方を検知する。

    `update_profile`/`main_agent`/`respond` の guided/ストリーミング生成で
    共通に使う暴走検知(vLLM が稀に同一断片を無限反復するケースへの安全弁)。
    """

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


@dataclass(frozen=True, slots=True)
class GuardResult:
    accepted: bool
    rule: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConstraintGuardResult:
    constraints: tuple[ConstraintDraft, ...]
    unmodeled: tuple[UnmodeledItem, ...]


def validate_and_normalize_constraints(
    values: Sequence[ConstraintDraft],
    spots: Mapping[str, SpotFact],
    *,
    created_at_version: int,
    used_ids: Iterable[str] = (),
) -> ConstraintGuardResult:
    """17 述語と実在引数を検査し、無効値は `unmodeled` へ移す(C4)。"""

    normalized = normalize_constraints(
        [value.model_dump(mode="python", exclude_none=True) for value in values],
        spots,
        created_at_version=max(1, created_at_version),
        used_ids=used_ids,
    )
    constraints = tuple(
        ConstraintDraft.model_validate(value.model_dump(mode="python"))
        for value in normalized.constraints
    )
    unmodeled = tuple(
        UnmodeledItem(
            text=value.source_text or f"{value.pred}: {value.args}",
            reason=value.reason,
        )
        for value in normalized.unmodeled
    )
    return ConstraintGuardResult(constraints=constraints, unmodeled=unmodeled)


def normalize_revert_ops(ops: Sequence[dict[str, object]]) -> tuple[list[dict[str, object]], bool]:
    """`revert` が混在したら、それ以外を捨てる。"""

    reverts = [value for value in ops if value.get("op") == "revert"]
    if not reverts:
        return [dict(value) for value in ops], False
    return [dict(reverts[0])], len(ops) != 1 or len(reverts) != 1


def validate_response_spot_names(
    text: str,
    *,
    all_spot_names: Mapping[str, str],
    allowed_spot_ids: set[str],
) -> GuardResult:
    """DB 上の未提示 POI 名を `respond` が追加していないか照合する。"""

    unauthorized = sorted(
        name
        for spot_id, name in all_spot_names.items()
        if spot_id not in allowed_spot_ids and name and name in text
    )
    if unauthorized:
        return GuardResult(
            False,
            "closed_world_response",
            f"未提示の POI 名が応答に含まれます: {unauthorized}",
        )
    return GuardResult(True)


def evaluate_ask_user(
    question: AskUserArgs,
    *,
    ask_user_count: int,
    ask_streak: int,
    asked_slots: Sequence[str],
    resolved_ambiguities: Sequence[object] = (),
    allowed_spot_ids: set[str] | None = None,
    existing_spot_ids: set[str] | None = None,
) -> GuardResult:
    """`ask_user` の抑制ガード(§10 R4・A1〜A5)。

    メイン(`main_agent.py`)・レコメンド SA(`recommend_agent.py`)・知識検索 SA
    (`narration/search/agent.py` の ask コールバック経由)の 3 経路すべてが
    `ask_execution.execute_ask_user` を通じて本関数を呼ぶ。カウンタ
    (`ask_user_count`/`ask_streak`)はターン全体で合算する(R4・A2)。

    `allowed_spot_ids`/`existing_spot_ids` を渡したときだけ A4(clarify の
    選択肢が実在 spot_id に解決できるか)を検査する。呼び出し元が spot_id
    以外の具体値(レコメンド SA の enum 値・知識検索の文書ラベル等)を使う
    場合は渡さなくてよい(A4 はメインエージェントの clarify 専用の防御)。
    """

    if ask_user_count >= MAX_ASK_USER_PER_TURN:
        return GuardResult(
            False, "R4", "このターンで質問できる回数の上限(2回)に達しました"
        )
    if ask_streak >= MAX_ASK_STREAK:
        return GuardResult(False, "A2", "質問を含むターンが連続しています")
    if (
        question.kind == "preference"
        and question.slot is not None
        and question.slot.value in asked_slots
    ):
        return GuardResult(
            False,
            "A1",
            f"slot={question.slot.value} は質問済みです",
        )
    if not 2 <= len(question.options) <= 4:
        return GuardResult(False, "A3", "選択肢は 2〜4 個にしてください")
    if any(
        not option.label.strip() or not option.value.strip()
        for option in question.options
    ):
        return GuardResult(False, "A3", "空の選択肢は使えません")
    if question.kind == "clarify":
        if allowed_spot_ids is not None or existing_spot_ids is not None:
            allowed = allowed_spot_ids or set()
            existing = existing_spot_ids or set()
            for option in question.options:
                if option.value not in existing or option.value not in allowed:
                    return GuardResult(
                        False,
                        "A4",
                        f"選択肢が具体値に解決しません: {option.value}",
                    )
        normalized_surface = _normalize_surface(question.surface or "")
        if normalized_surface in {
            _normalize_surface(surface)
            for surface in _resolved_surfaces(resolved_ambiguities)
        }:
            return GuardResult(False, "A5", "同じ曖昧さは既に聞き返しています")
    return GuardResult(True)


def _resolved_surfaces(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, Mapping) and isinstance(value.get("surface"), str):
            result.append(value["surface"])
    return result


def _normalize_surface(value: str) -> str:
    return "".join(value.casefold().split())
