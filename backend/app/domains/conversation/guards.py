"""P1〜P8・G1〜G9 と横断不変条件を集めた純粋ガード群。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domains.conversation.state import ProfileState, SpotFact
from app.domains.conversation.types import (
    AskUserArgs,
    Clarification,
    ConstraintDraft,
    Intent,
    ReferenceResolution,
    ScoreAdjustment,
    SelectionHint,
    UnmodeledItem,
)
from app.domains.itinerary.predicates import normalize_constraints

_ALLOWED_INTERPRETATIONS = frozenset(
    {"all_matches", "single_match", "current_itinerary", "last_candidates"}
)
_REFERENCE_PATTERN = re.compile(
    r"^\$(?P<step>[1-9]\d*)\.(?P<field>spot_ids|itinerary)"
    r"(?:\[:(?P<limit>[1-8])\])?$"
)


@dataclass(frozen=True, slots=True)
class GuardResult:
    accepted: bool
    rule: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConstraintGuardResult:
    constraints: tuple[ConstraintDraft, ...]
    unmodeled: tuple[UnmodeledItem, ...]


@dataclass(frozen=True, slots=True)
class ClassificationCount:
    extracted: int
    constraints: int
    score_adjustments: int
    selection_hints: int
    unmodeled: int

    @property
    def classified(self) -> int:
        return (
            self.constraints
            + self.score_adjustments
            + self.selection_hints
            + self.unmodeled
        )


def validate_classification_completeness(
    *,
    extracted_count: int,
    constraints: Sequence[ConstraintDraft],
    score_adjustments: Sequence[ScoreAdjustment],
    selection_hints: Sequence[SelectionHint],
    unmodeled: Sequence[UnmodeledItem],
) -> GuardResult:
    """選好・制約の抽出要素が 4 経路に過不足なく入ったか検査する。"""

    count = ClassificationCount(
        extracted=extracted_count,
        constraints=len(constraints),
        score_adjustments=len(score_adjustments),
        selection_hints=len(selection_hints),
        unmodeled=len(unmodeled),
    )
    if count.extracted != count.classified:
        return GuardResult(
            False,
            "classification_completeness",
            (
                f"抽出数 {count.extracted} と分類済み数 "
                f"{count.classified} が一致しません"
            ),
        )
    return GuardResult(True)


def validate_and_normalize_constraints(
    values: Sequence[ConstraintDraft],
    spots: Mapping[str, SpotFact],
    *,
    created_at_version: int,
    used_ids: Iterable[str] = (),
) -> ConstraintGuardResult:
    """17 述語と実在引数を検査し、無効値は経路 4 へ移す。"""

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


def validate_reference_closed_world(
    reference: ReferenceResolution,
    *,
    allowed_spot_ids: set[str],
    existing_spot_ids: set[str],
) -> GuardResult:
    if reference.spot_id not in existing_spot_ids:
        return GuardResult(
            False,
            "closed_world",
            f"実在しない spot_id です: {reference.spot_id}",
        )
    if reference.spot_id not in allowed_spot_ids:
        return GuardResult(
            False,
            "reference_scope",
            f"現在の文脈から参照できない spot_id です: {reference.spot_id}",
        )
    return GuardResult(True)


def validate_preference_question(
    question: AskUserArgs,
    *,
    asked_slots: Sequence[str],
    ask_streak: int,
    intent: Intent | None,
    profile: ProfileState,
    has_non_question_step: bool,
    question_count: int = 1,
) -> GuardResult:
    """ADR-0007 G1〜G5 を順に適用する。"""

    if question_count > 1:
        return GuardResult(False, "G1", "1 ターンに聞ける選好質問は 1 問です")
    if question.slot.value in asked_slots:
        return GuardResult(False, "G2", f"slot={question.slot.value} は質問済みです")
    if ask_streak >= 2:
        return GuardResult(False, "G3", "選好質問が 2 ターン連続しています")
    major_slots_empty = (
        profile.party is None
        and profile.mobility is None
        and not profile.interests
    )
    if (
        intent is Intent.RECOMMEND
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
    if any(not value.strip() for value in question.options):
        return GuardResult(False, "G5", "空の選択肢は使えません")
    return GuardResult(True)


def validate_clarification(
    clarification: Clarification,
    *,
    allowed_spot_ids: set[str],
    existing_spot_ids: set[str],
    resolved_ambiguities: Sequence[Any],
    clarify_streak: int,
    has_viable_plan: bool,
) -> GuardResult:
    """ADR-0010 G6〜G9。G1〜G5 は意図的に適用しない。"""

    if not 2 <= len(clarification.options) <= 4:
        return GuardResult(False, "G6", "具体的な選択肢が 2〜4 個ありません")
    for option in clarification.options:
        resolution = option.resolves_to
        if resolution.kind == "spot_id":
            if (
                resolution.value not in existing_spot_ids
                or resolution.value not in allowed_spot_ids
            ):
                return GuardResult(
                    False,
                    "G6",
                    (
                        "選択肢が参照可能な spot_id に解決しません: "
                        f"{resolution.value}"
                    ),
                )
        elif resolution.value not in _ALLOWED_INTERPRETATIONS:
            return GuardResult(
                False,
                "G6",
                f"未定義の解釈です: {resolution.value}",
            )
    normalized_surface = _normalize_surface(clarification.surface)
    if normalized_surface in {
        _normalize_surface(surface)
        for surface in _resolved_surfaces(resolved_ambiguities)
    }:
        return GuardResult(False, "G7", "同じ曖昧さは既に聞き返しています")
    if clarify_streak >= 1:
        return GuardResult(False, "G8", "聞き返しは 1 ターンまでです")
    if has_viable_plan:
        return GuardResult(False, "G9", "妥当な plan を出せるため聞き返しません")
    return GuardResult(True)


def normalize_revert_ops(ops: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """`revert` が混在したら、それ以外を捨てる。"""

    reverts = [value for value in ops if value.get("op") == "revert"]
    if not reverts:
        return [dict(value) for value in ops], False
    return [dict(reverts[0])], len(ops) != 1 or len(reverts) != 1


def parse_step_reference(value: str) -> tuple[int, str, int | None] | None:
    match = _REFERENCE_PATTERN.fullmatch(value)
    if match is None:
        return None
    field = match.group("field")
    limit_text = match.group("limit")
    if field == "itinerary" and limit_text is not None:
        return None
    return int(match.group("step")), field, int(limit_text) if limit_text else None


def validate_response_spot_names(
    text: str,
    *,
    all_spot_names: Mapping[str, str],
    allowed_spot_ids: set[str],
) -> GuardResult:
    """DB 上の未提示 POI 名を respond が追加していないか照合する。"""

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


def _resolved_surfaces(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, Mapping) and isinstance(value.get("surface"), str):
            result.append(value["surface"])
    return result


def _normalize_surface(value: str) -> str:
    return "".join(value.casefold().split())
