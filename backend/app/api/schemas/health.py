"""ヘルスチェック API の契約。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DependencyHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    latency_ms: float
    detail: str | None = None


class OSRMDependencyHealth(DependencyHealth):
    build: str | None


class HealthDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    db: DependencyHealth
    vllm: DependencyHealth
    osrm_car: OSRMDependencyHealth
    osrm_foot: OSRMDependencyHealth


class GeoDataHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spot_approach: int
    travel_times_car: int
    travel_times_foot: int


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    dependencies: HealthDependencies
    geo_data: GeoDataHealth
