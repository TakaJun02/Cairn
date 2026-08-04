"""static.spots の API 表現を読み出す。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import Spot


@dataclass(frozen=True)
class SpotData:
    spot_id: str
    kind: str
    category: str
    name_ja: str
    tags_ja: list[str]
    lat: float
    lon: float
    social_proof: str | None


@dataclass(frozen=True)
class SpotsSnapshot:
    spots: list[SpotData]
    latest_updated_at: datetime | None


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_spots(self) -> SpotsSnapshot:
        geometry = cast(Spot.geom, Geometry("POINT", srid=4326))
        rows = (
            await self.session.execute(
                select(
                    Spot.spot_id,
                    Spot.kind,
                    Spot.category,
                    Spot.name_ja,
                    Spot.tags_ja,
                    func.ST_Y(geometry).label("lat"),
                    func.ST_X(geometry).label("lon"),
                    Spot.social_proof,
                    Spot.updated_at,
                ).order_by(Spot.spot_id)
            )
        ).all()
        return SpotsSnapshot(
            spots=[
                SpotData(
                    spot_id=row.spot_id,
                    kind=row.kind,
                    category=row.category,
                    name_ja=row.name_ja,
                    tags_ja=list(row.tags_ja),
                    lat=float(row.lat),
                    lon=float(row.lon),
                    social_proof=_japanese_value(row.social_proof),
                )
                for row in rows
            ],
            latest_updated_at=max((row.updated_at for row in rows), default=None),
        )


def _japanese_value(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    japanese = value.get("ja")
    return japanese if isinstance(japanese, str) else None
