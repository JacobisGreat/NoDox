"""Per-audit dollar accountant.

A single CostTracker is created per session and carried in
``session.data["cost_tracker"]``. Every Anthropic call records its usage
here; pipelines must call ``can_spend`` before initiating spend so that
they can short-circuit and emit ``budget_exceeded`` instead of overrunning.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class CostTracker:
    def __init__(
        self,
        global_ceiling_usd: float,
        scope_budgets: dict[str, float] | None = None,
        pro_in: float = 1.25,
        pro_out: float = 10.0,
        fast_in: float = 0.30,
        fast_out: float = 2.50,
        pro_model_id: str = "",
        fast_model_id: str = "",
    ) -> None:
        self._global_ceiling = float(global_ceiling_usd)
        self._scope_budgets: dict[str, float] = dict(scope_budgets or {})
        self._pro_in = float(pro_in)
        self._pro_out = float(pro_out)
        self._fast_in = float(fast_in)
        self._fast_out = float(fast_out)
        self._pro_id = pro_model_id
        self._fast_id = fast_model_id

        self._totals: dict[str, float] = defaultdict(float)
        self._totals["global"] = 0.0
        self._external: list[dict[str, Any]] = []
        self._llm_calls: list[dict[str, Any]] = []

    # --- pricing -------------------------------------------------------

    def _prices_for(self, model: str) -> tuple[float, float]:
        # Match by exact id first, then by family substring as a fallback
        # so renamed model snapshots still get sensible pricing.
        if model and self._pro_id and model == self._pro_id:
            return self._pro_in, self._pro_out
        if model and self._fast_id and model == self._fast_id:
            return self._fast_in, self._fast_out
        lowered = (model or "").lower()
        if "pro" in lowered:
            return self._pro_in, self._pro_out
        if "flash" in lowered or "fast" in lowered or "haiku" in lowered:
            return self._fast_in, self._fast_out
        # Unknown model — assume pro pricing (more conservative).
        return self._pro_in, self._pro_out

    @staticmethod
    def _compute(
        input_tokens: int,
        output_tokens: int,
        in_price: float,
        out_price: float,
    ) -> float:
        return (
            (input_tokens / 1_000_000.0) * in_price
            + (output_tokens / 1_000_000.0) * out_price
        )

    # --- recording -----------------------------------------------------

    def record_llm(self, usage: dict, scope: str = "global") -> float:
        model = usage.get("model", "") or ""
        in_price, out_price = self._prices_for(model)
        delta = self._compute(
            int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0),
            in_price,
            out_price,
        )
        self._totals["global"] += delta
        if scope and scope != "global":
            self._totals[scope] += delta
        self._llm_calls.append(
            {
                "model": model,
                "scope": scope,
                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
                "cost_usd": delta,
            }
        )
        return delta

    # Back-compat alias — old call sites use record_anthropic.
    record_anthropic = record_llm

    def record_external(
        self, service: str, cost_usd: float, scope: str = "global"
    ) -> None:
        delta = float(cost_usd)
        self._totals["global"] += delta
        if scope and scope != "global":
            self._totals[scope] += delta
        self._external.append(
            {"service": service, "scope": scope, "cost_usd": delta}
        )

    # --- queries -------------------------------------------------------

    @property
    def total_usd(self) -> float:
        return self._totals["global"]

    def scope_total(self, scope: str) -> float:
        return self._totals.get(scope, 0.0)

    def can_spend(self, estimated_usd: float, scope: str = "global") -> bool:
        if self._totals["global"] + estimated_usd > self._global_ceiling:
            return False
        if scope and scope != "global" and scope in self._scope_budgets:
            if self._totals[scope] + estimated_usd > self._scope_budgets[scope]:
                return False
        return True

    def remaining(self, scope: str = "global") -> float:
        if scope == "global" or scope not in self._scope_budgets:
            return max(0.0, self._global_ceiling - self._totals["global"])
        return max(0.0, self._scope_budgets[scope] - self._totals[scope])

    def snapshot(self) -> dict:
        return {
            "global_ceiling_usd": self._global_ceiling,
            "global_spent_usd": self._totals["global"],
            "remaining_usd": self.remaining("global"),
            "scope_totals": {k: v for k, v in self._totals.items() if k != "global"},
            "scope_budgets": dict(self._scope_budgets),
            "llm_calls": list(self._llm_calls),
            "external_calls": list(self._external),
        }
