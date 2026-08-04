"""DB ドライバを import せず利用できる旅程 repository の値型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.itinerary.solver import PlanningSpot, TravelTimeMatrix
from app.domains.itinerary.types import Itinerary


class ItineraryRepositoryError(RuntimeError):
    """DB 上の旅程版に不整合または競合がある。"""


class ItineraryNotFoundError(ItineraryRepositoryError):
    """現在版または指定版が存在しない。"""


class ItineraryVersionConflictError(ItineraryRepositoryError):
    """同じユーザーに対する並行編集で親版が変わった。"""

    def __init__(self, message: str, *, current_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version


@dataclass(frozen=True, slots=True)
class ItineraryVersion:
    user_id: int
    version: int
    parent_version: int | None
    is_current: bool
    itinerary: Itinerary
    constraints: list[dict[str, Any]]
    origin: str
    created_by_message_id: int | None


@dataclass(frozen=True, slots=True)
class PlanningData:
    spots: dict[str, PlanningSpot]
    travel_times: TravelTimeMatrix
