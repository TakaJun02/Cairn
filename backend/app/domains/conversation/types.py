"""対話パイプライン内部の閉じた契約。

API の入出力ではなく、`agent_planning_phase.md` §15.7・§18 をノード間で
共有する型である。
Tool 固有ドメインにはこのモジュールを import させない。
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.itinerary.types import PredEnum
from app.domains.recommendation.types import Mobility, Pace, Party, PreferenceKey


class ConversationModel(BaseModel):
    """未知フィールドを対話状態へ紛れ込ませない。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ToolName(StrEnum):
    RECOMMEND = "recommend"
    PLAN_ITINERARY = "plan_itinerary"
    EDIT_ITINERARY = "edit_itinerary"
    SEARCH_KNOWLEDGE = "search_knowledge"
    ASK_USER = "ask_user"


class Intent(StrEnum):
    RECOMMEND = "recommend"
    PLAN = "plan"
    EDIT = "edit"
    QA = "qa"
    PROFILE_ONLY = "profile_only"
    CHITCHAT = "chitchat"
    UNCLEAR = "unclear"


class ResponseMode(StrEnum):
    EXPLANATION = "explanation"
    QUESTION = "question"
    FAILURE = "failure"


class Slot(StrEnum):
    ONBOARDING = "onboarding"
    PARTY = "party"
    MOBILITY = "mobility"
    PACE = "pace"
    INTERESTS = "interests"
    DATES = "dates"
    ORIGIN = "origin"


class ToolErrorCode(StrEnum):
    PRECONDITION_UNMET = "precondition_unmet"
    REFERENCE_UNRESOLVED = "reference_unresolved"
    EMPTY_RESULT = "empty_result"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL = "internal"


class ReferenceResolution(ConversationModel):
    surface: str = Field(min_length=1)
    spot_id: str = Field(min_length=1)


class ProfileDelta(ConversationModel):
    interests: dict[PreferenceKey, float] = Field(default_factory=dict)
    party: Party | None = None
    mobility: Mobility | None = None
    pace: Pace | None = None
    avoid: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("interests")
    @classmethod
    def validate_interests(
        cls, values: dict[PreferenceKey, float]
    ) -> dict[PreferenceKey, float]:
        for key, value in values.items():
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"interests.{key.value} は -1.0〜1.0 にしてください")
        return values

    @field_validator("avoid")
    @classmethod
    def normalize_avoid(cls, values: list[str]) -> list[str]:
        return _deduplicate_nonempty(values)

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ConstraintDraft(ConversationModel):
    """LLM 抽出直後の制約。

    `pred` は Pydantic enum にせず文字列で受ける。guided decoding 後にも
    未知値をコードで `unmodeled` へ移す不変条件を
    テスト可能にするためである。
    """

    id: str | None = None
    pred: str
    args: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    source_text: str = ""
    source_message_id: int | None = None
    created_at_version: int | None = None
    handling: Literal["dsl"] = "dsl"

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("constraint.weight は有限の 0 以上にしてください")
        return value


class ScoreAdjustment(ConversationModel):
    spot_id: str = Field(min_length=1)
    delta: float
    why: str = ""
    handling: Literal["weight"] = "weight"

    @field_validator("delta")
    @classmethod
    def validate_delta(cls, value: float) -> float:
        if not math.isfinite(value) or not -0.5 <= value <= 0.5:
            raise ValueError("score_adjustments.delta は -0.5〜0.5 にしてください")
        return value


class SelectionHint(ConversationModel):
    text: str = Field(min_length=1)
    handling: Literal["selection"] = "selection"


class UnmodeledItem(ConversationModel):
    text: str = Field(min_length=1)
    handling: Literal["unmodeled"] = "unmodeled"
    reason: str | None = None


class PlanStep(ConversationModel):
    """検証前の plan 手。

    Tool 名を `str` のまま保持するのは、guided enum とは別に P2 をコードで
    強制し、違反理由を `rejected_steps` に残すためである。
    """

    id: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class UnderstandOutput(ConversationModel):
    """N2 の guided JSON。宣言順は生成時の推論順そのものである。"""

    references: list[ReferenceResolution] = Field(default_factory=list)
    profile_delta: ProfileDelta | None = None
    constraints: list[ConstraintDraft] = Field(default_factory=list)
    constraints_remove: list[str] = Field(default_factory=list)
    score_adjustments: list[ScoreAdjustment] = Field(default_factory=list)
    selection_hints: list[SelectionHint] = Field(default_factory=list)
    unmodeled: list[UnmodeledItem] = Field(default_factory=list)
    intent: Intent
    plan: list[PlanStep] = Field(default_factory=list)


class RecommendArgs(ConversationModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    k: int = Field(default=5, ge=1, le=8)
    exclude: list[str] = Field(default_factory=list)


class PlanItineraryArgs(ConversationModel):
    # 不足値は §20.2 に従い planner が既定値で補う。
    # そのため、ここでは空も受ける。
    days: list[dict[str, Any]] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)


class EditItineraryArgs(ConversationModel):
    ops: list[dict[str, Any]] = Field(default_factory=list)


class SearchKnowledgeArgs(ConversationModel):
    request: str = Field(min_length=1)
    spot_id: str | None = None


class AskUserOption(ConversationModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class AskUserArgs(ConversationModel):
    kind: Literal["preference", "clarify"]
    slot: Slot | None = None
    surface: str | None = None
    reason: str = Field(min_length=1)
    # 2〜4 件かどうかは Pydantic ではなく G5 として理由つきで判定する。
    options: list[AskUserOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind_shape(self) -> AskUserArgs:
        if self.kind == "preference":
            if self.slot is None:
                raise ValueError("kind=preference のとき slot が必要です")
            if self.surface is not None:
                raise ValueError("kind=preference のとき surface は指定できません")
        else:
            if self.surface is None or not self.surface.strip():
                raise ValueError("kind=clarify のとき surface が必要です")
            if self.slot is not None:
                raise ValueError("kind=clarify のとき slot は指定できません")
        return self


class AskUserResult(ConversationModel):
    answer: str = Field(min_length=1)
    answered_by: Literal["chip", "free_text"]
    slot: Slot | None = None
    surface: str | None = None


class ToolError(ConversationModel):
    code: ToolErrorCode
    message_ja: str
    recoverable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(ConversationModel):
    step_id: int
    tool: ToolName
    data: dict[str, Any] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)


def constraint_to_mapping(value: ConstraintDraft) -> dict[str, Any]:
    """ItineraryService へ渡せる JSON 互換 dict にする。"""

    return value.model_dump(mode="json", exclude_none=True)


def pred_values() -> list[str]:
    return [value.value for value in PredEnum]


def preference_values() -> list[str]:
    return [value.value for value in PreferenceKey]


def _deduplicate_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = raw.strip()
        if value and value not in result:
            result.append(value)
    return result
