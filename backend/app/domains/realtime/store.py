"""`app.spot_realtime` を唯一のリアルタイム状態として読み書きする。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Spot, SpotRealtime

_VALID_VALUES = frozenset({0, 1, 2})


class RealtimeSpotNotFoundError(LookupError):
    """static.spots に存在しない spot_id。"""


@dataclass(frozen=True, slots=True)
class RealtimeSpotData:
    """現在値。DB 行が無い場合も weather/congestion=None として表現する。"""

    spot_id: str
    weather: int | None
    congestion: int | None
    source: str | None
    updated_at: datetime | None

    def as_dict(self) -> dict[str, object]:
        return {
            "spot_id": self.spot_id,
            "weather": self.weather,
            "congestion": self.congestion,
            "source": self.source,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RealtimeStore:
    """spot_realtime の upsert と static.spots を基準にした outer join 読み出し。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, spot_id: str) -> RealtimeSpotData:
        row = (
            await self.session.execute(
                select(
                    Spot.spot_id,
                    SpotRealtime.weather,
                    SpotRealtime.congestion,
                    SpotRealtime.source,
                    SpotRealtime.updated_at,
                )
                .outerjoin(SpotRealtime, SpotRealtime.spot_id == Spot.spot_id)
                .where(Spot.spot_id == spot_id)
            )
        ).one_or_none()
        if row is None:
            raise RealtimeSpotNotFoundError(spot_id)
        return RealtimeSpotData(*row)

    async def list_all(self) -> list[RealtimeSpotData]:
        rows = (
            await self.session.execute(
                select(
                    Spot.spot_id,
                    SpotRealtime.weather,
                    SpotRealtime.congestion,
                    SpotRealtime.source,
                    SpotRealtime.updated_at,
                )
                .outerjoin(SpotRealtime, SpotRealtime.spot_id == Spot.spot_id)
                .order_by(Spot.spot_id)
            )
        ).all()
        return [RealtimeSpotData(*row) for row in rows]

    async def get_many(self, spot_ids: Sequence[str]) -> list[RealtimeSpotData]:
        """入力順を維持する。manifest にない DB 行は unknown として返す。"""

        if not spot_ids:
            return []
        rows = (
            await self.session.execute(
                select(SpotRealtime).where(SpotRealtime.spot_id.in_(set(spot_ids)))
            )
        ).scalars()
        by_id = {
            row.spot_id: RealtimeSpotData(
                row.spot_id,
                row.weather,
                row.congestion,
                row.source,
                row.updated_at,
            )
            for row in rows
        }
        return [
            by_id.get(spot_id, RealtimeSpotData(spot_id, None, None, None, None))
            for spot_id in spot_ids
        ]

    async def set(
        self,
        spot_id: str,
        *,
        weather: int | None,
        congestion: int | None,
        source: str = "simulated",
        updated_at: datetime | None = None,
    ) -> RealtimeSpotData:
        if not await self.session.scalar(select(Spot.spot_id).where(Spot.spot_id == spot_id)):
            raise RealtimeSpotNotFoundError(spot_id)
        await self._upsert(
            spot_id,
            weather=weather,
            congestion=congestion,
            source=source,
            updated_at=updated_at,
        )
        return await self.get(spot_id)

    async def set_events(
        self,
        events: Iterable[tuple[str, int | None, int | None]],
        *,
        updated_at: datetime | None = None,
    ) -> None:
        """検証済みシナリオの due event を同じトランザクションで適用する。"""

        timestamp = updated_at or datetime.now(UTC)
        for spot_id, weather, congestion in events:
            await self._upsert(
                spot_id,
                weather=weather,
                congestion=congestion,
                source="simulated",
                updated_at=timestamp,
            )

    async def _upsert(
        self,
        spot_id: str,
        *,
        weather: int | None,
        congestion: int | None,
        source: str,
        updated_at: datetime | None,
    ) -> None:
        _validate_value(weather, "weather")
        _validate_value(congestion, "congestion")
        if source not in {"sensor", "simulated"}:
            raise ValueError("source は sensor または simulated にしてください")
        timestamp = updated_at or datetime.now(UTC)
        statement = insert(SpotRealtime).values(
            spot_id=spot_id,
            weather=weather,
            congestion=congestion,
            source=source,
            updated_at=timestamp,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[SpotRealtime.spot_id],
                set_={
                    "weather": statement.excluded.weather,
                    "congestion": statement.excluded.congestion,
                    "source": statement.excluded.source,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )


def _validate_value(value: int | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or value not in _VALID_VALUES):
        raise ValueError(f"{name} は 0, 1, 2, None のいずれかにしてください")
