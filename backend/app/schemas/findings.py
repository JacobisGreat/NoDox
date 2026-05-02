"""Finding dataclass — the unit of evidence emitted by every pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


@dataclass
class Finding:
    source: str
    evidence_chain: list[str]
    confidence: float  # 0.0 to 1.0
    risk_level: RiskLevel
    remediation: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
