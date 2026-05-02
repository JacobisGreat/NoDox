from typing import Any
from pydantic import BaseModel


class Finding(BaseModel):
    source: str
    evidence_chain: list[str]
    confidence: float
    risk_level: str  # LOW | MEDIUM | HIGH | CRITICAL
    remediation: str | None = None
    metadata: dict[str, Any] = {}


class RemediationItem(BaseModel):
    priority: int
    action: str
    reason: str
    risk_level: str


class AggregatorResult(BaseModel):
    exposure_score: int
    summary: str
    remediation_list: list[RemediationItem]
    error: str | None = None
