"""レッグ経路 REST API の契約。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SpotRouteEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spot_id: str = Field(min_length=1)


class CoordinateRouteEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


RouteEndpoint = Annotated[
    SpotRouteEndpoint | CoordinateRouteEndpoint,
    Field(union_mode="left_to_right"),
]


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_endpoint: RouteEndpoint = Field(alias="from")
    to: RouteEndpoint


class RouteSegmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["car", "foot"]
    distance_m: int = Field(ge=0)
    duration_sec: int = Field(ge=0)
    from_idx: int = Field(ge=0)
    to_idx: int = Field(ge=0)


class RouteFeatureProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["car", "foot"]
    from_idx: int = Field(ge=0)
    to_idx: int = Field(ge=0)


class LineStringGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["LineString"]
    coordinates: list[list[float]] = Field(min_length=2)


class RouteFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Feature"]
    properties: RouteFeatureProperties
    geometry: LineStringGeometry


class RouteFeatureCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["FeatureCollection"]
    features: list[RouteFeature] = Field(min_length=1)


class RouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: UUID
    mode_summary: Literal["car", "foot", "car+foot"]
    distance_m: int = Field(ge=0)
    duration_sec: int = Field(ge=0)
    segments: list[RouteSegmentResponse] = Field(min_length=1)
    geojson: RouteFeatureCollection
