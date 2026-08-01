"""TTN フェアユースを DB 行ロックで守るダウンリンク予約。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

MAX_DOWNLINKS_PER_UTC_DAY = 10
MIN_DOWNLINK_INTERVAL = timedelta(minutes=5)

_BUDGET_EXCEEDED = "downlink_budget_exceeded"
_TOO_SOON = "downlink_too_soon"
logger = logging.getLogger("app.realtime.scheduler")


@dataclass(frozen=True, slots=True)
class DownlinkDecision:
    allowed: bool
    reason: str | None
    count: int


def evaluate_downlink(
    *,
    count: int,
    last_sent: datetime | None,
    now: datetime,
) -> DownlinkDecision:
    """永続化済み状態から、次の 1 通を送ってよいか純粋に判定する。"""

    current = _as_utc(now)
    if count < 0:
        raise ValueError("count は 0 以上にしてください")
    if count >= MAX_DOWNLINKS_PER_UTC_DAY:
        return DownlinkDecision(False, _BUDGET_EXCEEDED, count)
    if last_sent is not None and current - _as_utc(last_sent) < MIN_DOWNLINK_INTERVAL:
        return DownlinkDecision(False, _TOO_SOON, count)
    return DownlinkDecision(True, None, count + 1)


async def reserve_downlink(
    session: AsyncSession,
    device_id: str,
    *,
    now: datetime | None = None,
) -> DownlinkDecision:
    """UTC 日の行をロックし、許可した場合だけ count/last_sent を進める。"""

    if not device_id.strip():
        raise ValueError("device_id は空にできません")
    from app.db_models import LoraDownlink

    current = _as_utc(now or datetime.now(UTC))
    sent_date = current.date()
    await session.execute(
        insert(LoraDownlink)
        .values(device_id=device_id, sent_date=sent_date, count=0, last_sent=None)
        .on_conflict_do_nothing(
            index_elements=[LoraDownlink.device_id, LoraDownlink.sent_date]
        )
    )
    row = await session.scalar(
        select(LoraDownlink)
        .where(
            LoraDownlink.device_id == device_id,
            LoraDownlink.sent_date == sent_date,
        )
        .with_for_update()
    )
    if row is None:  # pragma: no cover - INSERT と同じトランザクションなので到達しない
        raise RuntimeError("lora_downlinks の予約行を取得できませんでした")
    decision = evaluate_downlink(count=row.count, last_sent=row.last_sent, now=current)
    if decision.allowed:
        row.count = decision.count
        row.last_sent = current
        await session.flush()
    else:
        logger.warning(
            decision.reason,
            extra={
                "event": decision.reason,
                "device_id": device_id,
                "sent_date": sent_date.isoformat(),
                "count": row.count,
                "last_sent": row.last_sent,
            },
        )
    return decision


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("日時は timezone-aware にしてください")
    return value.astimezone(UTC)
