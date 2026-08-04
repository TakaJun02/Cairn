"""lora_downlinks の UTC 日別永続カウンタを compose DB で検証する。"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.db import dispose_engine, session_scope
from app.db_models import LoraDownlink
from app.domains.realtime.scheduler import reserve_downlink

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="RUN_DB_INTEGRATION=1 のとき compose DB に対して実行する",
)


async def test_counter_rejects_eleventh_and_resets_on_next_utc_day() -> None:
    device_id = f"scheduler-test-{uuid4()}"
    first = datetime(2026, 8, 2, 0, tzinfo=UTC)
    try:
        async with session_scope() as session:
            decisions = [
                await reserve_downlink(
                    session,
                    device_id,
                    now=first + timedelta(minutes=5 * index),
                )
                for index in range(11)
            ]
            next_day = await reserve_downlink(
                session,
                device_id,
                now=first + timedelta(days=1),
            )

        assert all(decision.allowed for decision in decisions[:10])
        assert decisions[9].count == 10
        assert decisions[10].allowed is False
        assert decisions[10].reason == "downlink_budget_exceeded"
        assert next_day.allowed is True
        assert next_day.count == 1

        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(LoraDownlink)
                    .where(LoraDownlink.device_id == device_id)
                    .order_by(LoraDownlink.sent_date)
                )
            ).all()
            assert [(row.sent_date.isoformat(), row.count) for row in rows] == [
                ("2026-08-02", 10),
                ("2026-08-03", 1),
            ]
    finally:
        async with session_scope() as session:
            await session.execute(
                delete(LoraDownlink).where(LoraDownlink.device_id == device_id)
            )
        await dispose_engine()
