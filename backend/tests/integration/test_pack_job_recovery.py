"""古い running ジョブの起動時回復を compose DB で検証する。"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.db import dispose_engine, session_scope
from app.db_models import PackJob, User
from app.jobs.worker import recover_stale_jobs

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


async def test_stale_running_job_returns_to_queued() -> None:
    now = datetime.now(UTC)
    user_name = f"pack-recovery-{uuid4()}"
    job_id = uuid4()
    try:
        async with session_scope() as session:
            user = User(user_name=user_name, api_token=f"test-{uuid4()}")
            session.add(user)
            await session.flush()
            session.add(
                PackJob(
                    id=job_id,
                    pack_id=uuid4(),
                    user_id=user.id,
                    itinerary_version=1,
                    epoch=0,
                    state="running",
                    params={},
                    params_hash=str(uuid4()),
                    progress={"done": 2, "total": 5, "failed": 0},
                    updated_at=now - timedelta(minutes=11),
                )
            )
        async with session_scope() as session:
            assert await recover_stale_jobs(session, now=now) == 1
            row = await session.scalar(select(PackJob).where(PackJob.id == job_id))
            assert row is not None
            assert row.state == "queued"
            assert row.progress == {"done": 2, "total": 5, "failed": 0}
    finally:
        async with session_scope() as session:
            user_id = await session.scalar(select(User.id).where(User.user_name == user_name))
            if user_id is not None:
                await session.execute(delete(User).where(User.id == user_id))
        await dispose_engine()

