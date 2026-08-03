"""TurnState と、スレッド永続状態の SQLAlchemy 境界。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.conversation.types import (
    ConstraintDraft,
    Intent,
    PlanStep,
    ProfileDelta,
    ReferenceResolution,
    ScoreAdjustment,
    SelectionHint,
    ToolResult,
    UnmodeledItem,
)
from app.domains.itinerary.types import Itinerary


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileState(StateModel):
    interests: dict[str, float] = Field(default_factory=dict)
    party: str | None = None
    mobility: str | None = None
    pace: str | None = None
    avoid: list[str] = Field(default_factory=list)
    liked_spots: list[str] = Field(default_factory=list)
    rejected_spots: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class ItineraryState(StateModel):
    itinerary: Itinerary
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    parent_version: int | None = None

    @property
    def version(self) -> int:
        return self.itinerary.version


class CandidateReference(StateModel):
    spot_id: str
    name_ja: str
    rank: int = Field(ge=1)


class MessageState(StateModel):
    id: int
    seq: int
    role: Literal["user", "assistant"]
    content: str
    status: Literal["complete", "partial", "failed"]
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SpotFact(StateModel):
    spot_id: str
    name_ja: str
    kind: str
    aliases_ja: list[str] = Field(default_factory=list)
    tags_ja: list[str] = Field(default_factory=list)


class RejectedStep(StateModel):
    step_id: int | None
    tool: str | None
    rule: str
    reason: str
    step: dict[str, Any] = Field(default_factory=dict)


class SkippedStep(StateModel):
    step_id: int
    tool: str
    code: str
    reason: str


class DegradedState(StateModel):
    code: str
    stage: str
    message: str


class ContextSnapshot(StateModel):
    thread_id: int
    profile: ProfileState
    itinerary: ItineraryState | None
    messages: list[MessageState]
    last_candidates: list[CandidateReference]
    presented_spot_ids: list[str]
    asked_slots: list[str]
    ask_streak: int
    pending_ask: dict[str, Any] | None
    resolved_ambiguities: list[Any]
    pending_constraints: list[dict[str, Any]]
    realtime: dict[str, dict[str, int | None]]
    spots: dict[str, SpotFact]
    tag_vocabulary: list[str] = Field(default_factory=list)


class TurnState(StateModel):
    """§15.7 の欄をノード順に並べた、ターン内だけの状態。"""

    # N1 load_context
    turn_id: str
    thread_id: int
    user_id: int
    utterance: str
    profile: ProfileState
    itinerary: ItineraryState | None = None
    history: str = ""
    history_tokens: int = 0
    last_candidates: list[CandidateReference] = Field(default_factory=list)
    presented_spot_ids: list[str] = Field(default_factory=list)
    asked_slots: list[str] = Field(default_factory=list)
    ask_streak: int = 0
    resolved_ambiguities: list[Any] = Field(default_factory=list)
    pending_constraints: list[dict[str, Any]] = Field(default_factory=list)
    realtime: dict[str, dict[str, int | None]] = Field(default_factory=dict)
    spot_id_vocab: list[str] = Field(default_factory=list)
    spot_names: dict[str, str] = Field(default_factory=dict)
    spot_catalog: dict[str, SpotFact] = Field(default_factory=dict)
    tag_vocabulary: list[str] = Field(default_factory=list)
    default_origin_spot_id: str | None = None
    tool_results: list[dict[str, Any]] = Field(default_factory=list)

    # N2 understand
    intent: Intent | None = None
    plan: list[PlanStep] = Field(default_factory=list)
    profile_delta: ProfileDelta | None = None
    constraints: list[ConstraintDraft] = Field(default_factory=list)
    constraints_remove: list[str] = Field(default_factory=list)
    score_adjustments: list[ScoreAdjustment] = Field(default_factory=list)
    selection_hints: list[SelectionHint] = Field(default_factory=list)
    unmodeled: list[UnmodeledItem] = Field(default_factory=list)
    references: list[ReferenceResolution] = Field(default_factory=list)
    understand_attempts: int = 0
    understand_failed: bool = False
    understand_failures: list[str] = Field(default_factory=list)

    # N3 validate_plan
    accepted_steps: list[PlanStep] = Field(default_factory=list)
    rejected_steps: list[RejectedStep] = Field(default_factory=list)
    llm_budget_step: int | None = None
    assumptions: list[str] = Field(default_factory=list)

    # N4 act
    step_results: dict[int, ToolResult] = Field(default_factory=dict)
    skipped_steps: list[SkippedStep] = Field(default_factory=list)
    aborted_at: int | None = None
    should_end_turn: bool = False
    pending_ask: dict[str, Any] | None = None
    degraded: list[DegradedState] = Field(default_factory=list)

    # N5 respond
    assistant_text: str = ""
    respond_status: Literal["complete", "partial", "failed"] = "complete"

    # 全ノード
    log_fields: dict[str, Any] = Field(default_factory=dict)


class ConversationStateError(RuntimeError):
    """ユーザーに対応する 1:1 状態を再構築できない。"""
