"""ガイダンスパックのジョブ・成果物 API 契約。"""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PackAssetVariant(StrEnum):
    BASE = "base"
    WEATHER_CLOUDY = "weather_cloudy"
    WEATHER_RAIN = "weather_rain"
    CONGESTION_MID = "congestion_mid"
    CONGESTION_HIGH = "congestion_high"


class PackAssetRole(StrEnum):
    VISIT = "visit"
    PASS_BY = "pass_by"


PackJobState = Literal["queued", "running", "ready", "partial", "failed"]


class PackOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_along_poi: bool = True
    along_poi_limit: int = Field(default=20, ge=0, le=100)


class PackCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itinerary_version: int = Field(ge=1)
    options: PackOptions = Field(default_factory=PackOptions)


class PackCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    pack_id: UUID
    state: PackJobState
    total: int = Field(ge=0)


class PackProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    done: int = Field(ge=0)
    total: int = Field(ge=0)
    failed: int = Field(ge=0)


class PackFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spot_id: str | None = None
    variant: PackAssetVariant | None = None
    reason: str
    detail: str | None = None


class PackJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: PackJobState
    progress: PackProgress
    pack_id: UUID
    failures: list[PackFailure]


class PackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: PackJobState
    itinerary_version: int = Field(ge=1)
    manifest_url: str | None
    total_bytes: int = Field(ge=0)
    missing: list[PackFailure]

