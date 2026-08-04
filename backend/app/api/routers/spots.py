"""ETag 対応の静的スポット一覧 API。"""

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.schemas.spots import SpotResponse
from app.core.db import get_db_session
from app.domains.catalog import CatalogRepository

router = APIRouter(
    prefix="/api/v1/spots",
    tags=["spots"],
    dependencies=[Depends(get_current_user)],
)


def get_catalog_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CatalogRepository:
    return CatalogRepository(session)


@router.get("", response_model=list[SpotResponse])
async def list_spots(
    response: Response,
    repository: Annotated[CatalogRepository, Depends(get_catalog_repository)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> list[SpotResponse] | Response:
    snapshot = await repository.list_spots()
    etag = _spots_etag(snapshot.latest_updated_at.isoformat() if snapshot.latest_updated_at else "")
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return [SpotResponse.model_validate(spot) for spot in snapshot.spots]


def _spots_etag(latest_updated_at: str) -> str:
    digest = hashlib.sha256(latest_updated_at.encode("utf-8")).hexdigest()
    return f'"{digest}"'


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    candidates = {candidate.strip() for candidate in if_none_match.split(",")}
    return "*" in candidates or etag in candidates or f"W/{etag}" in candidates
