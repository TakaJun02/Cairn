"""レッグ経路の作成・取得 API。"""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.routes import RouteRequest, RouteResponse
from app.core.config import Settings, get_settings
from app.core.db import get_db_session
from app.domains.geo.osrm import OSRMClient, OSRMError
from app.domains.geo.repo import GeoRepository, RouteRecord
from app.domains.geo.routes import (
    MissingApproachError,
    RouteBuildError,
    RouteInputError,
    RouteService,
    UnknownSpotError,
)

router = APIRouter(prefix="/api/v1/routes", tags=["routes"])


def get_geo_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> GeoRepository:
    return GeoRepository(session)


async def get_route_service(
    repository: Annotated[GeoRepository, Depends(get_geo_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[RouteService]:
    async with OSRMClient(settings) as osrm:
        yield RouteService(repository, osrm, settings)


@router.post("", response_model=RouteResponse)
async def create_route(
    request: RouteRequest,
    service: Annotated[RouteService, Depends(get_route_service)],
) -> RouteResponse:
    try:
        record = await service.get_or_create(
            request.from_endpoint.model_dump(),
            request.to.model_dump(),
        )
    except UnknownSpotError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RouteInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except MissingApproachError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except RouteBuildError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except OSRMError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return _route_response(record)


@router.get("/{route_id}", response_model=RouteResponse)
async def get_route(
    route_id: UUID,
    repository: Annotated[GeoRepository, Depends(get_geo_repository)],
) -> RouteResponse:
    record = await repository.get_route_by_id(route_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="route がありません",
        )
    return _route_response(record)


def _route_response(record: RouteRecord) -> RouteResponse:
    return RouteResponse.model_validate(
        {
            "route_id": record.route_id,
            "mode_summary": record.mode_summary,
            "distance_m": record.distance_m,
            "duration_sec": record.duration_sec,
            "segments": record.segments,
            "geojson": record.geojson,
        }
    )
