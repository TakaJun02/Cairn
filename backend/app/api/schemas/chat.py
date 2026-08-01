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


class ClarificationResolutionRequest(ApiModel):
    surface: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ChatRequest(ApiModel):
    message: str = Field(min_length=1)
    resolves: ClarificationResolutionRequest | None = None

    @field_validator("message", mode="before")
    @classmethod
    def strip_message(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class PlanStep(ApiModel):
    id: int = Field(ge=1)
    tool: Literal[
        "recommend",
        "plan_itinerary",
        "edit_itinerary",
        "search_knowledge",
        "ask_user",
    ]


class PlanState(ApiModel):
    kind: Literal["plan"]
    steps: list[PlanStep]


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
    options: list[str] = Field(min_length=2, max_length=4)


class ClarificationOption(ApiModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ClarifyState(ApiModel):
    kind: Literal["clarify"]
    surface: str = Field(min_length=1)
    options: list[ClarificationOption] = Field(min_length=1)


class ProfileState(ApiModel):
    kind: Literal["profile"]
    profile: dict[str, Any]


class SearchingState(ApiModel):
    kind: Literal["searching"]
    text: str = Field(min_length=1)


ChatState = Annotated[
    PlanState
    | CandidatesState
    | ItineraryState
    | AskUserState
    | ClarifyState
    | ProfileState
    | SearchingState,
    Field(discriminator="kind"),
]


class TokenData(ApiModel):
    text: str


class ErrorStage(StrEnum):
    UNDERSTAND = "understand"
    VALIDATE_PLAN = "validate_plan"
    ACT = "act"
    RESPOND = "respond"
    PERSIST = "persist"


class ErrorCode(StrEnum):
    UNDERSTAND_FAILED = "understand_failed"
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
