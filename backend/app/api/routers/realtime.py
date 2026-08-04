"""計画フェーズ用 realtime GET と実験用の管理 API。"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.schemas.realtime import (
    RealtimeSpotResponse,
    RealtimeSpotUpdate,
    SimulatorActionRequest,
    SimulatorStateResponse,
)
from app.core.db import get_db_session
from app.domains.realtime.simulator import (
    RealtimeScenario,
    SimulatorError,
    SimulatorService,
)
from app.domains.realtime.store import (
    RealtimeSpotData,
    RealtimeSpotNotFoundError,
    RealtimeStore,
)

router = APIRouter(
    prefix="/api/v1/realtime",
    tags=["realtime"],
    dependencies=[Depends(get_current_user)],
)


def get_realtime_store(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RealtimeStore:
    return RealtimeStore(session)


def get_simulator_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SimulatorService:
    return SimulatorService(session)


@router.get("/spots/{spot_id}", response_model=RealtimeSpotResponse)
async def get_realtime_spot(
    spot_id: str,
    response: Response,
    store: Annotated[RealtimeStore, Depends(get_realtime_store)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> RealtimeSpotResponse | Response:
    try:
        value = await store.get(spot_id)
    except RealtimeSpotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="spot がありません",
        ) from exc
    etag = realtime_etag(value)
    if etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return RealtimeSpotResponse.model_validate(value)


@router.post("/spots/{spot_id}", response_model=RealtimeSpotResponse)
async def set_realtime_spot(
    spot_id: str,
    update: RealtimeSpotUpdate,
    response: Response,
    store: Annotated[RealtimeStore, Depends(get_realtime_store)],
) -> RealtimeSpotResponse:
    try:
        value = await store.set(
            spot_id,
            weather=update.weather,
            congestion=update.congestion,
            source="simulated",
        )
    except RealtimeSpotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="spot がありません",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    response.headers["ETag"] = realtime_etag(value)
    return RealtimeSpotResponse.model_validate(value)


@router.post("/simulator/{action}", response_model=SimulatorStateResponse)
async def control_simulator(
    action: str,
    service: Annotated[SimulatorService, Depends(get_simulator_service)],
    request: SimulatorActionRequest | None = None,
) -> SimulatorStateResponse:
    payload = request or SimulatorActionRequest()
    try:
        if action == "load":
            if payload.scenario is None:
                raise SimulatorError("load には scenario が必要です")
            state = await service.load(RealtimeScenario.model_validate(payload.scenario))
        elif action == "start":
            state = await service.start(speed=payload.speed or 1.0)
        elif action == "stop":
            state = await service.stop()
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="action は load, start, stop のいずれかです",
            )
    except (SimulatorError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return SimulatorStateResponse.model_validate(state.model_dump())


def realtime_etag(value: RealtimeSpotData) -> str:
    canonical = json.dumps(
        value.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f'"{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    candidates = {candidate.strip() for candidate in if_none_match.split(",")}
    return "*" in candidates or etag in candidates or f"W/{etag}" in candidates
