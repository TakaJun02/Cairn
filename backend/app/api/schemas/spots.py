"""スポット一覧 API の契約。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SpotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    spot_id: str
    kind: Literal["poi", "facility"]
    category: str
    name_ja: str
    tags_ja: list[str]
    lat: float
    lon: float
    social_proof: str | None
