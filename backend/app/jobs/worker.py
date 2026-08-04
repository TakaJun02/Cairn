"""DB ポーリング、単一ジョブ実行、10分 stale のクラッシュ復帰。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import session_scope
from app.db_models import PackJob
from app.domains.packs.planner import ensure_job_transition
from app.domains.packs.runner import PackRunner

POLL_INTERVAL_SECONDS = 2.0
STALE_RUNNING_AFTER = timedelta(minutes=10)

logger = logging.getLogger("app.jobs.worker")


async def recover_stale_jobs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stale_after: timedelta = STALE_RUNNING_AFTER,
) -> int:
    """起動時だけ、古い running を queued へ戻して成功済み行を再利用する。"""

    current = now or datetime.now(UTC)
    cutoff = current - stale_after
    rows = (
        await session.scalars(
            select(PackJob)
            .where(PackJob.state == "running", PackJob.updated_at <= cutoff)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for row in rows:
        progress = dict(row.progress)
        progress.pop("retry_requested", None)
        row.state = "queued"
        row.progress = progress
        row.updated_at = current
    await session.flush()
    return len(rows)


async def claim_next_job(session: AsyncSession) -> UUID | None:
    """queued を優先し、再要求で running に戻したジョブも同じ1本枠で取得する。"""

    row = await session.scalar(
        select(PackJob)
        .where(PackJob.state == "queued")
        .order_by(PackJob.created_at, PackJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is not None:
        ensure_job_transition(row.state, "running")
        row.state = "running"
    else:
        retry_rows = (
            await session.scalars(
                select(PackJob)
                .where(PackJob.state == "running")
                .order_by(PackJob.updated_at, PackJob.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
        row = next(
            (
                candidate
                for candidate in retry_rows
                if bool(candidate.progress.get("retry_requested"))
            ),
            None,
        )
        if row is None:
            return None
    progress = dict(row.progress)
    progress.pop("retry_requested", None)
    progress.pop("job_failure", None)
    row.progress = progress
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return row.id


class PackJobWorker:
    """app プロセスに1つだけ置く研究規模のジョブワーカー。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        runner: PackRunner | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.settings = settings or get_settings()
        self.runner = runner or PackRunner(self.settings)
        self.poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        async with session_scope(self.settings) as session:
            recovered = await recover_stale_jobs(session)
        if recovered:
            logger.warning("pack_jobs_recovered", extra={"count": recovered})
        self._task = asyncio.create_task(self._run_loop(), name="pack-job-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with session_scope(self.settings) as session:
                    job_id = await claim_next_job(session)
                if job_id is not None:
                    await self.runner.run(job_id)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - ワーカー自体を停止させない
                logger.exception("pack_worker_iteration_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
