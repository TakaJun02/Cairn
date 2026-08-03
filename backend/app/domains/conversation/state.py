"""TurnState と、スレッド永続状態の SQLAlchemy 境界。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.conversation.types import (
    ProfileDelta,
    ScoreAdjustment,
    ToolResult,
    TrajectoryStep,
)
from app.domains.itinerary.types import Itinerary

__all__ = [
    "CandidateReference",
    "ContextSnapshot",
    "ConversationStateError",
    "DegradedState",
    "ItineraryState",
    "MessageState",
    "ProfileState",
    "SpotFact",
    "StateModel",
    "TurnState",
]


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
    history_summary: str = ""
    summarized_until_message_id: int | None = None


class TurnState(StateModel):
    """ReAct 構成(段2)のターン内だけの状態。

    `Docs/30_design/agent_react_architecture.md` の新パイプライン
    `load_context → update_profile → main_agent(ReAct ループ) → respond → persist`
    に沿ってノード順に並べている。
    """

    # ① load_context(決定的)
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
    # `ask_user` の HITL 抑制ガード(§10 A1/A2/A5)。読み込み時の値を、
    # ターン内で質問が実行されるたびにメインループ/SA が更新する。
    asked_slots: list[str] = Field(default_factory=list)
    ask_streak: int = 0
    resolved_ambiguities: list[Any] = Field(default_factory=list)
    pending_ask: dict[str, Any] | None = None
    pending_constraints: list[dict[str, Any]] = Field(default_factory=list)
    # このターンで実行できた ask_user の回数(メイン・SA 合算。R4=2)。
    ask_user_count: int = 0
    # ask_user への回答(user 行として persist する。§7・data_model.md §4.4)。
    qa_answers: list[dict[str, Any]] = Field(default_factory=list)
    realtime: dict[str, dict[str, int | None]] = Field(default_factory=dict)
    spot_id_vocab: list[str] = Field(default_factory=list)
    spot_names: dict[str, str] = Field(default_factory=dict)
    spot_catalog: dict[str, SpotFact] = Field(default_factory=dict)
    tag_vocabulary: list[str] = Field(default_factory=list)
    default_origin_spot_id: str | None = None

    # ② update_profile(LLM 1 回)
    profile_delta: ProfileDelta | None = None
    score_adjustments: list[ScoreAdjustment] = Field(default_factory=list)

    # ③ メインエージェント(ReAct ループ)
    trajectory: list[TrajectoryStep] = Field(default_factory=list)
    step_results: dict[int, ToolResult] = Field(default_factory=dict)
    executed_tool_count: int = 0
    main_agent_turns: int = 0
    main_agent_failed: bool = False
    main_agent_failures: list[str] = Field(default_factory=list)
    degraded: list[DegradedState] = Field(default_factory=list)

    # ④ respond
    responded: bool = False
    assistant_text: str = ""
    respond_status: Literal["complete", "partial", "failed"] = "complete"

    # 全ノード
    log_fields: dict[str, Any] = Field(default_factory=dict)


class ConversationStateError(RuntimeError):
    """ユーザーに対応する 1:1 状態を再構築できない。"""
