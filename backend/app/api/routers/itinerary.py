"""現在旅程の取得と、版ポインタだけを動かす undo / redo API。"""

from __future__ import annotations

from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.schemas.chat import (
    ItineraryConflictDetail,
    ItineraryConflictResponse,
    ItineraryState,
    ItineraryVersionRequest,
)
from app.core.db import get_db_session
from app.domains.itinerary.ops import calculate_diff
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.itinerary.repo_types import (
    ItineraryNotFoundError,
    ItineraryVersion,
    ItineraryVersionConflictError,
)
from app.domains.itinerary.types import Diff
from app.domains.users import UserData

router = APIRouter(
    prefix="/api/v1/itinerary",
    tags=["itinerary"],
)


def get_itinerary_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ItineraryRepository:
    return ItineraryRepository(session)


@router.get("", response_model=ItineraryState)
async def get_itinerary(
    current_user: Annotated[UserData, Depends(get_current_user)],
    repository: Annotated[ItineraryRepository, Depends(get_itinerary_repository)],
) -> ItineraryState:
    current = await repository.get_current(current_user.id)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="現在の旅程がありません",
        )
    return _itinerary_state(current)


@router.post(
    "/undo",
    response_model=ItineraryState,
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ItineraryConflictResponse,
            "description": "現在版の競合、または戻せる版がない",
        }
    },
)
async def undo_itinerary(
    body: ItineraryVersionRequest,
    current_user: Annotated[UserData, Depends(get_current_user)],
    repository: Annotated[ItineraryRepository, Depends(get_itinerary_repository)],
) -> ItineraryState:
    source = await repository.get_version(
        current_user.id,
        body.expected_current_version,
    )
    try:
        target = await repository.revert(
            current_user.id,
            expected_current_version=body.expected_current_version,
        )
    except (ItineraryNotFoundError, ItineraryVersionConflictError) as exc:
        await _raise_conflict(repository, current_user.id, str(exc))
    return _itinerary_state(
        target,
        diff=(
            calculate_diff(source.itinerary, target.itinerary)
            if source is not None
            else Diff()
        ),
    )


@router.post(
    "/redo",
    response_model=ItineraryState,
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ItineraryConflictResponse,
            "description": "現在版の競合、または進める版がない",
        }
    },
)
async def redo_itinerary(
    body: ItineraryVersionRequest,
    current_user: Annotated[UserData, Depends(get_current_user)],
    repository: Annotated[ItineraryRepository, Depends(get_itinerary_repository)],
) -> ItineraryState:
    source = await repository.get_version(
        current_user.id,
        body.expected_current_version,
    )
    try:
        target = await repository.redo(
            current_user.id,
            expected_current_version=body.expected_current_version,
        )
    except (ItineraryNotFoundError, ItineraryVersionConflictError) as exc:
        await _raise_conflict(repository, current_user.id, str(exc))
    return _itinerary_state(
        target,
        diff=(
            calculate_diff(source.itinerary, target.itinerary)
            if source is not None
            else Diff()
        ),
    )


def _itinerary_state(
    version: ItineraryVersion,
    *,
    phase: Literal["provisional", "final"] = "final",
    diff: Diff | None = None,
) -> ItineraryState:
    itinerary = version.itinerary.model_dump(mode="json", by_alias=True)
    return ItineraryState.model_validate(
        {
            "kind": "itinerary",
            "phase": phase,
            "version": version.version,
            "itinerary": itinerary,
            "diff": (diff or Diff()).model_dump(mode="json", by_alias=True),
            "concessions": [
                concession.model_dump(mode="json")
                for concession in version.itinerary.concessions
            ],
        }
    )


async def _raise_conflict(
    repository: ItineraryRepository,
    user_id: int,
    message: str,
) -> NoReturn:
    current = await repository.get_current(user_id)
    detail = ItineraryConflictDetail(
        message=message,
        current_version=current.version if current is not None else None,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail.model_dump(mode="json"),
    )
