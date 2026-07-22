from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, HttpUrl, field_validator


class Item(BaseModel):
    source: Literal["ted", "oeffentlichevergabe", "signals"]
    external_id: str
    title: str
    buyer: str | None = None
    country: str  # ISO 3166-1 alpha-3, e.g. DEU, AUT, CHE
    cpv: list[str] = []
    notice_type: Literal["pin", "cn", "can", "signal"] | None = None
    published_at: datetime | None = None
    deadline: datetime | None = None
    url: HttpUrl  # always from raw data, never LLM-generated
    raw: dict[str, Any] = {}
    signal_kind: Literal["re_tender", "job", "web_news"] | None = None

    @property
    def hash(self) -> str:
        return hashlib.sha256(f"{self.source}::{self.external_id}".encode()).hexdigest()


class Score(BaseModel):
    relevance: float
    profile_spediteur: float
    profile_kep: float
    reasoning: str
    tags: list[str] = []

    @field_validator("relevance", "profile_spediteur", "profile_kep")
    @classmethod
    def must_be_0_to_1(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Score must be between 0 and 1, got {v}")
        return v

    @property
    def best(self) -> float:
        return max(self.profile_spediteur, self.profile_kep)
