"""コードが強制するガード群(`Docs/30_design/agent_react_architecture.md` §10)。

段2(ReAct 化)で、旧 P1〜P8(一括プラン検証)・G1〜G9 のうち `$N` 参照解決・
3経路分類完全性チェックは不要になった(メインエージェントは毎周 1 手だけを
書き、前段結果への参照は行わない。名前解決は `name_resolution.py` が担う)。

残すもの:
- `validate_and_normalize_constraints`: メインエージェントが直接書く制約
  (述語 DSL 17 種)の検証。`plan_itinerary`/`edit_itinerary` アダプタが使う
- `normalize_revert_ops`: `edit_itinerary.ops` に `revert` が混じったときの
  排他化。メインループが Tool 実行前に使う
- `validate_response_spot_names`: `respond` のクローズドワールド検査
- `validate_ask_user`(G1〜G9)と `asked_slots`/`ask_streak`/
  `resolved_ambiguities` を使うユーティリティ: 段5 (`ask_user` の HITL 化)で
  使うため、今回は呼び出し元がなくても残す
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.domains.conversation.state import ProfileState, SpotFact
from app.domains.conversation.types import AskUserArgs, ConstraintDraft, UnmodeledItem
from app.domains.itinerary.predicates import normalize_constraints

_ALLOWED_INTERPRETATIONS = frozenset(
    {"all_matches", "single_match", "current_itinerary", "last_candidates"}
)


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


def validate_ask_user(
    question: AskUserArgs,
    *,
    asked_slots: Sequence[str],
    ask_streak: int,
    intent: str | None,
    profile: ProfileState,
    has_non_question_step: bool,
    question_count: int = 1,
    allowed_spot_ids: set[str] | None = None,
    existing_spot_ids: set[str] | None = None,
    resolved_ambiguities: Sequence[object] = (),
    has_viable_plan: bool = False,
) -> GuardResult:
    """段5で使う `ask_user` 抑制ガード(G1〜G9)。段2からの呼び出しはない。

    `intent` は旧 `Intent` enum(段2で廃止)の代わりに文字列で受ける。
    段5でメインループの「意図」概念を再設計する際に見直すこと。
    """

    if question_count > 1:
        return GuardResult(False, "G1", "1 ターンに聞ける質問は 1 問です")
    if (
        question.kind == "preference"
        and question.slot is not None
        and question.slot.value in asked_slots
    ):
        return GuardResult(
            False,
            "G2",
            f"slot={question.slot.value} は質問済みです",
        )
    if ask_streak >= 2:
        return GuardResult(False, "G3", "ask_user が 2 ターン連続しています")
    major_slots_empty = (
        profile.party is None
        and profile.mobility is None
        and not profile.interests
    )
    if (
        question.kind == "preference"
        and question.slot is not None
        and intent == "recommend"
        and not has_non_question_step
        and not (major_slots_empty and question.slot.value == "onboarding")
    ):
        return GuardResult(
            False,
            "G4",
            "推薦要求を選好質問だけで終えることはできません",
        )
    if not 2 <= len(question.options) <= 4:
        return GuardResult(False, "G5", "選択肢は 2〜4 個にしてください")
    if any(
        not option.label.strip() or not option.value.strip()
        for option in question.options
    ):
        return GuardResult(False, "G5", "空の選択肢は使えません")
    if question.kind == "clarify":
        allowed = allowed_spot_ids or set()
        existing = existing_spot_ids or set()
        for option in question.options:
            if option.value in _ALLOWED_INTERPRETATIONS:
                continue
            if option.value not in existing or option.value not in allowed:
                return GuardResult(
                    False,
                    "G6",
                    (
                        "選択肢が参照可能な spot_id または既定の解釈に"
                        f"解決しません: {option.value}"
                    ),
                )
        normalized_surface = _normalize_surface(question.surface or "")
        if normalized_surface in {
            _normalize_surface(surface)
            for surface in _resolved_surfaces(resolved_ambiguities)
        }:
            return GuardResult(False, "G7", "同じ曖昧さは既に聞き返しています")
    if has_viable_plan:
        return GuardResult(False, "G9", "妥当な plan を出せるため聞き返しません")
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
