"""リアルタイム状態とシミュレータ管理 API の schema。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RealtimeSpotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    spot_id: str
    weather: int | None
    congestion: int | None
    source: str | None
    updated_at: datetime | None


class RealtimeSpotUpdate(BaseModel):
    weather: int | None = Field(default=None, ge=0, le=2)
    congestion: int | None = Field(default=None, ge=0, le=2)


class SimulatorActionRequest(BaseModel):
    scenario: dict[str, Any] | None = None
    speed: float | None = Field(default=None, gt=0)


class SimulatorStateResponse(BaseModel):
    loaded: bool
    name: str | None
    running: bool
    speed: float
    elapsed_min: float
    next_event_index: int
    event_count: int
    virtual_time: datetime | None
