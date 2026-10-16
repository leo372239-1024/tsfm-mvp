from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator
import math


class AssetIn(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{2,80}$")
    name: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=30)
    cadence_seconds: int = Field(ge=1, le=86400)
    source_uri: str | None = Field(default=None, max_length=1000)


class PointIn(BaseModel):
    ts: datetime
    value: float
    quality: Literal["measured", "estimated"] = "measured"

    @field_validator("value")
    @classmethod
    def finite(cls, v):
        if not math.isfinite(v):
            raise ValueError("value must be finite")
        return v


class BackfillIn(BaseModel):
    points: list[PointIn] = Field(min_length=1, max_length=10000)


class ForecastIn(BaseModel):
    horizon: int = Field(default=8, ge=1, le=168)
    provider: Literal["auto", "timer", "local"] = "auto"


class FactIn(BaseModel):
    subject_id: str = Field(min_length=1, max_length=100)
    subject_label: str = Field(min_length=1, max_length=200)
    predicate: Literal["CONNECTS_TO", "CONTAINS", "PRODUCES", "MEASURES", "DOCUMENTS"]
    object_id: str = Field(min_length=1, max_length=100)
    object_label: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(min_length=8, max_length=1000)
    source_anchor: str = Field(min_length=1, max_length=200)
    source_excerpt: str = Field(min_length=1, max_length=1000)
    scope: Literal["reference", "site_supplied"] = "reference"

