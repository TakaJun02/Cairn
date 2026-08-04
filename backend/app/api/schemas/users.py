"""ユーザー登録・認証・スレッド復元の API 契約。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_name: str = Field(min_length=1, max_length=100)

    @field_validator("user_name", mode="before")
    @classmethod
    def strip_user_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    user_name: str
    token: str


class ProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    interests: dict[str, float]
    party: Literal["family_kids", "couple", "solo", "senior", "group"] | None
    mobility: Literal["avoid_walk", "short_walk_ok", "hike_ok"] | None
    pace: Literal["packed", "relaxed"] | None
    avoid: list[str]
    liked_spots: list[str]
    rejected_spots: list[dict[str, Any]]
    notes: str | None
    updated_at: datetime


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    user_name: str
    lora_device_id: str | None
    created_at: datetime
    updated_at: datetime
    profile: ProfileResponse


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    seq: int
    role: Literal["user", "assistant"]
    content: str
    meta: dict[str, Any]
    created_at: datetime


class CurrentItineraryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["itinerary"] = "itinerary"
    phase: Literal["final"] = "final"
    version: int
    itinerary: dict[str, Any]


class ThreadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[MessageResponse]
    itinerary: CurrentItineraryResponse | None
    profile: ProfileResponse
    pending: dict[str, Any] | None
