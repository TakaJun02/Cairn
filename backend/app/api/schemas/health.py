"""ヘルスチェック API の契約。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DependencyHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    latency_ms: float
    detail: str | None = None


class HealthDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    db: DependencyHealth
    vllm: DependencyHealth
    osrm_car: DependencyHealth
    osrm_foot: DependencyHealth


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    dependencies: HealthDependencies
