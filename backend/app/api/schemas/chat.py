"""チャット SSE と旅程 REST が共有する外部契約。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(ApiModel):
    message: str = Field(min_length=1)

    @field_validator("message", mode="before")
    @classmethod
    def strip_message(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ClarificationResolutionRequest(ApiModel):
    """`kind:"clarify"` のチップ回答(§1.4)。"""

    surface: str = Field(min_length=1)
    value: str = Field(min_length=1)


class AskUserResolutionRequest(ApiModel):
    """`kind:"preference"` のチップ回答(§1.4)。"""

    slot: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ChatAnswerRequest(ApiModel):
    """`POST /api/v1/chat/answer`(§1.4)。`resolves` はチップ経由のときだけ付く。"""

    answer: str = Field(min_length=1)
    resolves: AskUserResolutionRequest | ClarificationResolutionRequest | None = None

    @field_validator("answer", mode="before")
    @classmethod
    def strip_answer(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class StepState(ApiModel):
    """`state:step`(ADR-0019)。メインエージェントが手を実行する前後の実況。

    知識検索サブエージェントの内部実況(旧 `searching`)もここに統合される
    (`status="progress"`)。
    """

    kind: Literal["step"]
    tool: Literal[
        "recommend",
        "plan_itinerary",
        "edit_itinerary",
        "search_knowledge",
        "ask_user",
    ]
    status: Literal["started", "progress", "finished"]
    label_ja: str = Field(min_length=1)


class CandidateItem(ApiModel):
    spot_id: str = Field(min_length=1)
    name_ja: str = Field(min_length=1)
    reason_materials: dict[str, Any] = Field(default_factory=dict)


class CandidatesState(ApiModel):
    kind: Literal["candidates"]
    phase: Literal["provisional", "final"]
    items: list[CandidateItem]


class ItineraryDiff(ApiModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    moved: list[dict[str, Any]] = Field(default_factory=list)
    retimed: list[str] = Field(default_factory=list)


class ItineraryState(ApiModel):
    kind: Literal["itinerary"] = "itinerary"
    phase: Literal["provisional", "final"]
    version: int = Field(ge=1)
    itinerary: dict[str, Any]
    diff: ItineraryDiff = Field(default_factory=ItineraryDiff)
    concessions: list[dict[str, Any]] = Field(default_factory=list)
    # 未確認の前提(日付・起点等)。空配列なら前提なし(2026-08-04 追加。
    # クローズドワールド原則の明示的な例外 —
    # [agent_react_architecture.md §5](../../../../Docs/30_design/agent_react_architecture.md))。
    # undo/redo・GET /api/v1/itinerary の応答にも同じフィールド
    # が載る([chat_sse.md §1.2](../../../../Docs/40_api/chat_sse.md))。
    assumptions: list[str] = Field(default_factory=list)


class AskUserState(ApiModel):
    kind: Literal["ask_user"]
    slot: Literal[
        "onboarding",
        "party",
        "mobility",
        "pace",
        "interests",
        "dates",
        "origin",
    ]
    reason: str = Field(min_length=1)
    options: list[str] = Field(min_length=2, max_length=4)


class ClarificationOption(ApiModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ClarifyState(ApiModel):
    kind: Literal["clarify"]
    surface: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    options: list[ClarificationOption] = Field(min_length=1)


class ProfileState(ApiModel):
    kind: Literal["profile"]
    profile: dict[str, Any]


ChatState = Annotated[
    StepState
    | CandidatesState
    | ItineraryState
    | AskUserState
    | ClarifyState
    | ProfileState,
    Field(discriminator="kind"),
]


class TokenData(ApiModel):
    text: str


class ErrorStage(StrEnum):
    LOAD_CONTEXT = "load_context"
    UPDATE_PROFILE = "update_profile"
    MAIN_AGENT = "main_agent"
    RECOMMEND = "recommend"
    PLAN_ITINERARY = "plan_itinerary"
    EDIT_ITINERARY = "edit_itinerary"
    SEARCH_KNOWLEDGE = "search_knowledge"
    RESPOND = "respond"
    PERSIST = "persist"


class ErrorCode(StrEnum):
    MAIN_AGENT_FAILED = "main_agent_failed"
    MAIN_AGENT_DEGRADED = "main_agent_degraded"
    CONTEXT_BUDGET_HARD = "context_budget_hard"
    PRECONDITION_UNMET = "precondition_unmet"
    REFERENCE_UNRESOLVED = "reference_unresolved"
    EMPTY_RESULT = "empty_result"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL = "internal"
    SOLVER_TIMEOUT = "solver_timeout"
    RERANK_DEGRADED = "rerank_degraded"
    ROUTE_DEGRADED = "route_degraded"
    SELECTION_DEGRADED = "selection_degraded"
    RESPONSE_CLOSED_WORLD_VIOLATION = "response_closed_world_violation"
    RESPOND_FAILED = "respond_failed"
    PERSIST_FAILED = "persist_failed"
    STREAM_FAILED = "stream_failed"


class ErrorData(ApiModel):
    stage: ErrorStage
    code: ErrorCode
    degraded: bool
    message: str = Field(min_length=1)


class DoneData(ApiModel):
    turn_id: str = Field(min_length=1)
    message_id: int | None
    degraded: bool


class StateChatEvent(ApiModel):
    event: Literal["state"]
    data: ChatState


class TokenChatEvent(ApiModel):
    event: Literal["token"]
    data: TokenData


class ErrorChatEvent(ApiModel):
    event: Literal["error"]
    data: ErrorData


class DoneChatEvent(ApiModel):
    event: Literal["done"]
    data: DoneData


ChatEventValue = Annotated[
    StateChatEvent | TokenChatEvent | ErrorChatEvent | DoneChatEvent,
    Field(discriminator="event"),
]


class ChatEvent(RootModel[ChatEventValue]):
    """OpenAPI 型生成へ載せる、SSE 全イベントの discriminated union。"""


class ItineraryVersionRequest(ApiModel):
    expected_current_version: int = Field(ge=1)


class ItineraryConflictDetail(ApiModel):
    message: str
    current_version: int | None


class ItineraryConflictResponse(ApiModel):
    detail: ItineraryConflictDetail
