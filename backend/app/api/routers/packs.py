"""パック生成要求、ジョブ進捗、所有者限定メタ情報 API。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.schemas.packs import (
    PackCreateRequest,
    PackCreateResponse,
    PackFailure,
    PackJobResponse,
    PackProgress,
    PackResponse,
)
from app.core.config import Settings, get_settings
from app.core.db import get_db_session
from app.db_models import PackAsset, PackJob
from app.domains.packs.planner import (
    PackItineraryNotFoundError,
    PackJobData,
    PackPlanner,
    PackPlanningError,
)
from app.domains.packs.storage import PackStorage
from app.domains.users import UserData

router = APIRouter(tags=["packs"])


class PackNotFoundError(LookupError):
    """存在と所有者不一致を同じ 404 に畳む。"""


class PackApplicationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.storage = PackStorage(settings.packs_root)

    async def create(
        self,
        *,
        user_id: int,
        request: PackCreateRequest,
    ) -> PackJobData:
        job = await PackPlanner(self.session, self.settings).request_pack(
            user_id=user_id,
            itinerary_version=request.itinerary_version,
            options=request.options.model_dump(),
        )
        # yield 依存の後処理 commit より先に 202 が送られる実装でも、直後の
        # GET /jobs が必ず同じ行を観測できるよう API 境界で確定する。
        await self.session.commit()
        return job

    async def job(self, *, user_id: int, job_id: UUID) -> PackJobResponse:
        row = await self.session.scalar(
            select(PackJob).where(PackJob.id == job_id, PackJob.user_id == user_id)
        )
        if row is None:
            raise PackNotFoundError
        progress = dict(row.progress)
        return PackJobResponse(
            state=row.state,
            progress=PackProgress(
                done=max(0, int(progress.get("done", 0))),
                total=max(0, int(progress.get("total", 0))),
                failed=max(0, int(progress.get("failed", 0))),
            ),
            pack_id=row.pack_id,
            failures=await self._failures(row),
        )

    async def pack(self, *, user_id: int, pack_id: UUID) -> PackResponse:
        row = await self.session.scalar(
            select(PackJob).where(
                PackJob.pack_id == pack_id,
                PackJob.user_id == user_id,
            )
        )
        if row is None:
            raise PackNotFoundError
        manifest = (
            self.storage.read_manifest(pack_id)
            if row.state in {"ready", "partial"}
            else None
        )
        failures = await self._failures(row)
        if manifest is not None:
            failures = [PackFailure.model_validate(value) for value in manifest.get("missing", [])]
            total_bytes = max(0, int(manifest.get("total_bytes", 0)))
        else:
            total_bytes = int(
                await self.session.scalar(
                    select(func.coalesce(func.sum(PackAsset.bytes), 0)).where(
                        PackAsset.pack_id == pack_id
                    )
                )
                or 0
            )
        return PackResponse(
            state=row.state,
            itinerary_version=row.itinerary_version,
            manifest_url=(
                f"/packs/{pack_id}/manifest.json"
                if manifest is not None and row.state in {"ready", "partial"}
                else None
            ),
            total_bytes=total_bytes,
            missing=failures,
        )

    async def _failures(self, job: PackJob) -> list[PackFailure]:
        rows = (
            await self.session.scalars(
                select(PackAsset)
                .where(
                    PackAsset.pack_id == job.pack_id,
                    (PackAsset.narration_state == "failed")
                    | (PackAsset.audio_state.in_(["failed", "skipped"])),
                )
                .order_by(PackAsset.spot_id, PackAsset.variant)
            )
        ).all()
        values: list[PackFailure] = []
        for row in rows:
            error = row.error or ""
            reason, _, detail = error.partition(":")
            if not reason:
                reason = (
                    "narration_failed"
                    if row.narration_state == "failed"
                    else "tts_failed"
                )
            values.append(
                PackFailure(
                    spot_id=row.spot_id,
                    variant=row.variant,
                    reason=reason.strip(),
                    detail=detail.strip() or None,
                )
            )
        job_failure: Any = job.progress.get("job_failure")
        if isinstance(job_failure, dict) and job_failure.get("reason"):
            values.append(
                PackFailure(
                    reason=str(job_failure["reason"]),
                    detail=(
                        str(job_failure["detail"])
                        if job_failure.get("detail") is not None
                        else None
                    ),
                )
            )
        return values


def get_pack_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PackApplicationService:
    return PackApplicationService(session, settings)


@router.post(
    "/api/v1/packs",
    response_model=PackCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_pack(
    request: PackCreateRequest,
    current_user: Annotated[UserData, Depends(get_current_user)],
    service: Annotated[PackApplicationService, Depends(get_pack_service)],
) -> PackCreateResponse:
    try:
        job = await service.create(user_id=current_user.id, request=request)
    except PackItineraryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (PackPlanningError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return PackCreateResponse(
        job_id=job.job_id,
        pack_id=job.pack_id,
        state=job.state,
        total=int(job.progress.get("total", 0)),
    )


@router.get("/api/v1/jobs/{job_id}", response_model=PackJobResponse)
async def get_pack_job(
    job_id: UUID,
    current_user: Annotated[UserData, Depends(get_current_user)],
    service: Annotated[PackApplicationService, Depends(get_pack_service)],
) -> PackJobResponse:
    try:
        return await service.job(user_id=current_user.id, job_id=job_id)
    except PackNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="job がありません",
        ) from exc


@router.get("/api/v1/packs/{pack_id}", response_model=PackResponse)
async def get_pack(
    pack_id: UUID,
    current_user: Annotated[UserData, Depends(get_current_user)],
    service: Annotated[PackApplicationService, Depends(get_pack_service)],
) -> PackResponse:
    try:
        return await service.pack(user_id=current_user.id, pack_id=pack_id)
    except PackNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="pack がありません",
        ) from exc
