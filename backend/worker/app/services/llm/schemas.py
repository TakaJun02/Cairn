from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel


class SpotRef(BaseModel):
    spot_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    md_slug: Optional[str] = None
    playback: Optional[Literal["arrival", "pass_by"]] = None
    situation: Optional[Literal["weather_1", "weather_2", "congestion_1", "congestion_2"]] = None


class DescribeJob(BaseModel):
    job_id: str
    spot: SpotRef


class DescribeRequest(BaseModel):
    language: Literal["ja", "en", "zh"]
    jobs: List[DescribeJob]


class DescribeItem(BaseModel):
    job_id: Optional[str] = None
    spot_id: str
    playback: Optional[Literal["arrival", "pass_by"]] = None
    situation: Optional[Literal["weather_1", "weather_2", "congestion_1", "congestion_2"]] = None
    text: str


class DescribeResponse(BaseModel):
    items: List[DescribeItem]
