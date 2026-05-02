from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.audit_context import (
    BUDGET_CEILING,
    accumulate_finding,
    add_cost,
    set_pipeline_status,
)
from app.core.event_helpers import emit_cost_update, emit_finding, emit_pipeline_status
from app.core.session_store import SessionState


class BudgetExceededError(Exception):
    pass


class BasePipeline(ABC):
    name: str

    async def run(self, session: SessionState) -> None:
        await set_pipeline_status(session, self.name, "running")
        await emit_pipeline_status(session, self.name, "running")
        try:
            await self._execute(session)
            await set_pipeline_status(session, self.name, "complete")
            await emit_pipeline_status(session, self.name, "complete")
        except BudgetExceededError:
            await set_pipeline_status(session, self.name, "budget_exceeded")
            await emit_pipeline_status(session, self.name, "budget_exceeded")
        except Exception:
            await set_pipeline_status(session, self.name, "error")
            await emit_pipeline_status(session, self.name, "error")

    @abstractmethod
    async def _execute(self, session: SessionState) -> None: ...

    async def _emit_finding(self, session: SessionState, finding: dict) -> None:
        """Emit a finding immediately and accumulate it for the aggregator."""
        await accumulate_finding(session, {**finding, "pipeline": self.name})
        await emit_finding(session, self.name, finding)

    async def _charge(self, session: SessionState, amount: float) -> float:
        """Charge cost and raise BudgetExceededError if ceiling is hit."""
        new_total, over = await add_cost(session, amount)
        await emit_cost_update(session, new_total)
        if over:
            raise BudgetExceededError(f"Budget ceiling ${BUDGET_CEILING:.2f} reached")
        return new_total


class IdentityPipeline(BasePipeline):
    name = "identity"

    async def _execute(self, session: SessionState) -> None:
        # TODO: implement identity cross-reference pipeline
        # Available on session: session.data['access_token'], session.data['ig_user']
        # Helpers: self._emit_finding(session, finding_dict), self._charge(session, usd_amount)
        # Anthropic calls: use ANTHROPIC_SEMAPHORE from app.core.audit_context
        pass


class GeolocationPipeline(BasePipeline):
    name = "geolocation"

    async def _execute(self, session: SessionState) -> None:
        # TODO: implement EXIF + VLM geolocation pipeline
        pass


class WebFootprintPipeline(BasePipeline):
    name = "web_footprint"

    async def _execute(self, session: SessionState) -> None:
        # TODO: implement Google CSE + dorking pipeline
        # Max 15 queries, 1 req/sec — enforce via asyncio.sleep(1) between CSE calls
        pass


PIPELINES: list[BasePipeline] = [
    IdentityPipeline(),
    GeolocationPipeline(),
    WebFootprintPipeline(),
]
