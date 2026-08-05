"""現在旅程の取得と、版ポインタだけを動かす undo / redo API。"""

from __future__ import annotations

import logging
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
from app.domains.conversation.itinerary_digest import mask_concession_list
from app.domains.itinerary.ops import calculate_diff
from app.domains.itinerary.repo import ItineraryRepository
from app.domains.itinerary.repo_types import (
    ItineraryNotFoundError,
    ItineraryVersion,
    ItineraryVersionConflictError,
)
from app.domains.itinerary.types import Diff
from app.domains.users import UserData

logger = logging.getLogger(__name__)

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
    return await _itinerary_state(current, repository)


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
    diff = calculate_diff(source.itinerary, target.itinerary) if source is not None else Diff()
    await repository.commit()
    return await _itinerary_state(
        target,
        repository,
        diff=diff,
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
    diff = calculate_diff(source.itinerary, target.itinerary) if source is not None else Diff()
    await repository.commit()
    return await _itinerary_state(
        target,
        repository,
        diff=diff,
    )


async def _itinerary_state(
    version: ItineraryVersion,
    repository: ItineraryRepository,
    *,
    phase: Literal["provisional", "final"] = "final",
    diff: Diff | None = None,
) -> ItineraryState:
    itinerary = version.itinerary.model_dump(mode="json", by_alias=True)
    # 2026-08-04 追加([25 §1-7]): undo/redo・GET 応答の `concessions` にも
    # 送出層のマスクを適用する(旧形式の永続化済み譲歩文への防御。
    # chat_sse.md §1.2)。spot_id → name_ja は spots テーブルから取得する。
    # マスク対象の concessions がどこにも無ければ名前ロード自体をスキップ
    # する(2026-08-04 レビュー是正)。
    spot_names = await _spot_names(repository) if version.itinerary.concessions else {}
    concessions = mask_concession_list(
        [concession.model_dump(mode="json") for concession in version.itinerary.concessions],
        spot_names,
    )
    itinerary["concessions"] = concessions
    return ItineraryState.model_validate(
        {
            "kind": "itinerary",
            "phase": phase,
            "version": version.version,
            "itinerary": itinerary,
            "diff": (diff or Diff()).model_dump(mode="json", by_alias=True),
            "concessions": concessions,
            "assumptions": list(version.itinerary.assumptions),
        }
    )


async def _spot_names(repository: ItineraryRepository) -> dict[str, str]:
    # 2026-08-04 レビュー是正([25 §1-7]): `load_planning_data` は spots
    # 全カラム + travel_times 全件を読み込む重い処理で、ここでは名前解決
    # にしか使わない。軽量な `load_spot_names` に切り替える。commit 成功
    # 後の名前ロード失敗で undo/redo が 500 を返してはならないため、失敗
    # 時は空 dict で続行する(マスクは中立表記へ落ちる)。
    try:
        return await repository.load_spot_names()
    except Exception:  # noqa: BLE001 - 名前解決の失敗で undo/redo を落とさない
        logger.warning("spot_names のロードに失敗しました。中立表記で続行します", exc_info=True)
        return {}


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
