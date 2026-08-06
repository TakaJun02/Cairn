"""`agent_planning_phase.md` §18.2 に対応する旅程ドメイン型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class DomainModel(BaseModel):
    """内部契約に余分なキーを混ぜないための共通設定。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Mode(StrEnum):
    CAR = "car"
    FOOT = "foot"


class PredEnum(StrEnum):
    """制約 DSL の閉じた集合。順序も設計文書と一致させる。"""

    WEIGHT = "weight"
    REQUIRE = "require"
    EXCLUDE = "exclude"
    COUNT_AT_MOST = "count_at_most"
    COUNT_AT_LEAST = "count_at_least"
    FIRST = "first"
    LAST = "last"
    BEFORE = "before"
    NOT_CONSECUTIVE = "not_consecutive"
    SAME_DAY = "same_day"
    DIFFERENT_DAY = "different_day"
    TIME_WINDOW = "time_window"
    STAY_AT_LEAST = "stay_at_least"
    DAY_PART_LOAD = "day_part_load"
    MAX_LEG_MIN = "max_leg_min"
    MODE_PREF = "mode_pref"
    LUNCH_BREAK = "lunch_break"


class Constraint(DomainModel):
    id: str = Field(min_length=1)
    pred: PredEnum
    args: dict[str, Any] = Field(default_factory=dict)
    weight: float = 1.0
    source_message_id: int | None = None
    source_text: str = ""
    created_at_version: int = Field(default=1, ge=1)
    handling: Literal["dsl"] = "dsl"

    @field_validator("weight")
    @classmethod
    def finite_nonnegative_weight(cls, value: float) -> float:
        if value < 0 or value == float("inf") or value != value:
            raise ValueError("weight は有限の 0 以上にしてください")
        return value


class Concession(DomainModel):
    constraint_id: str
    pred: PredEnum
    args: dict[str, Any]
    violation: float
    message_ja: str


class LegFromPrev(DomainModel):
    mode: Mode
    min: int = Field(ge=0)
    route_id: str | None = None


class SpotEndpoint(DomainModel):
    kind: Literal["spot"] = "spot"
    spot_id: str


class ItineraryItem(DomainModel):
    seq: int = Field(ge=1)
    spot_id: str
    arrive_min: int = Field(ge=0)
    stay_min: int = Field(ge=0)
    depart_min: int = Field(ge=0)
    leg_from_prev: LegFromPrev
    locked: bool = False
    note: str | None = None


class ItineraryDay(DomainModel):
    date: str
    start_min: int = Field(ge=0)
    end_min: int = Field(ge=0)
    origin: SpotEndpoint
    destination: SpotEndpoint
    items: list[ItineraryItem] = Field(default_factory=list)


class Itinerary(DomainModel):
    days: list[ItineraryDay]
    concessions: list[Concession] = Field(default_factory=list)
    version: int = Field(default=0, ge=0)
    # 未確認の前提(日付・起点等)の日本語短文リスト。版ごとにコピーされ、
    # 永続化は itineraries.body(jsonb)に載るだけ(2026-08-04 追加。
    # Docs/30_design/agent_react_architecture.md §5・§14、
    # Docs/30_design/data_model.md §4.5.2)。
    assumptions: list[str] = Field(default_factory=list)


class Position(DomainModel):
    day: int = Field(ge=1)
    position: int = Field(ge=1)


class MovedItem(DomainModel):
    spot_id: str
    from_: Position = Field(alias="from")
    to: Position


class Diff(DomainModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    moved: list[MovedItem] = Field(default_factory=list)
    retimed: list[str] = Field(default_factory=list)


class ToolErrorCode(StrEnum):
    PRECONDITION_UNMET = "precondition_unmet"
    REFERENCE_UNRESOLVED = "reference_unresolved"
    EMPTY_RESULT = "empty_result"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL = "internal"


class ToolError(DomainModel):
    code: ToolErrorCode
    message_ja: str
    recoverable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class UnmodeledConstraint(DomainModel):
    """未知の述語・実在しない引数を無言で落とさないための返却形。"""

    pred: str
    args: dict[str, Any] = Field(default_factory=dict)
    source_text: str = ""
    reason: str
    handling: Literal["unmodeled"] = "unmodeled"


class AddOp(DomainModel):
    op: Literal["add"]
    targets: list[str] | str
    day: int | None = Field(default=None, ge=1)
    after: str | None = None


class RemoveOp(DomainModel):
    op: Literal["remove"]
    targets: list[str]


class MoveOp(DomainModel):
    op: Literal["move"]
    target: str
    day: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=1)


class ReplaceOp(DomainModel):
    op: Literal["replace"]
    target: str
    with_: str = Field(alias="with")


class LockOp(DomainModel):
    op: Literal["lock"]
    targets: list[str]
    locked: bool


class SetStayOp(DomainModel):
    op: Literal["set_stay"]
    target: str
    min: int = Field(gt=0)


class SetTimeOp(DomainModel):
    op: Literal["set_time"]
    target: str
    arrive: int | str | None = None
    depart: int | str | None = None


class RevertOp(DomainModel):
    op: Literal["revert"]
    to_version: int | None = Field(default=None, ge=1)


Op: TypeAlias = Annotated[
    AddOp | RemoveOp | MoveOp | ReplaceOp | LockOp | SetStayOp | SetTimeOp | RevertOp,
    Field(discriminator="op"),
]
OPS_ADAPTER = TypeAdapter(list[Op])


class PlanItineraryResult(DomainModel):
    itinerary: Itinerary
    alternatives: list[Itinerary]
    concessions: list[Concession]
    selection_used: bool
    spot_ids: list[str]
    unmodeled: list[UnmodeledConstraint] = Field(default_factory=list)
    # Service が確定した制約(再採番後・must_visit/op 由来の暗黙制約・revert 先の
    # 版の制約)をそのまま返す(2026-08-04、レビュー是正・裁定7: 後方互換な
    # 追加フィールド)。conversation 側はこれを正として
    # `state.itinerary.constraints`/`active_constraint_ids` を更新する
    # (Docs/30_design/agent_react_architecture.md §5)。
    constraints: list[Constraint] = Field(default_factory=list)


class EditItineraryResult(PlanItineraryResult):
    diff: Diff


def parse_ops(values: list[Op | dict[str, Any]]) -> list[Op]:
    """dict と既に検証済みの op を同じ入口で扱う。"""

    return OPS_ADAPTER.validate_python(values)


def parse_minute(value: int | str, *, field_name: str = "時刻") -> int:
    """Tool 境界の `HH:MM` を内部の 00:00 起算分へ一度だけ変換する。"""

    if isinstance(value, bool):
        raise ValueError(f"{field_name} は分の整数または HH:MM にしてください")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{field_name} は 0 以上にしてください")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} は分の整数または HH:MM にしてください")
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"{field_name} は HH:MM 形式にしてください")
    hour, minute = (int(part) for part in parts)
    if hour < 0 or minute < 0 or minute >= 60:
        raise ValueError(f"{field_name} が有効な時刻ではありません")
    return hour * 60 + minute
