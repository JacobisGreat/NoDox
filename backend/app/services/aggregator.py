from __future__ import annotations

import json

import httpx

from app.core.audit_context import ANTHROPIC_SEMAPHORE, add_cost
from app.core.config import get_settings
from app.core.event_helpers import emit_aggregator_done, emit_cost_update
from app.core.session_store import SessionState

AGGREGATION_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """\
You are a privacy risk analyst for ShieldClaw, a consent-based Instagram OSINT self-audit tool.
The user has audited their own public presence. Produce a structured risk assessment.

Respond with valid JSON only — no prose before or after.

Output schema:
{
  "exposure_score": <integer 0-100>,
  "summary": "<two sentences, screenshot-ready>",
  "remediation_list": [
    {
      "priority": <integer starting at 1>,
      "action": "<concrete action referencing specific URL/path/account>",
      "reason": "<why this matters, citing specific evidence>",
      "risk_level": "<HIGH|MEDIUM|LOW>"
    }
  ]
}

Exposure score weighting:
- location pinpointing: 0.4
- identity linking: 0.35
- breach exposures: 0.15
- weak-signal appearances: 0.1

Remediation rules:
- Every action must name a concrete surface: specific post URL to delete, specific settings path,
  specific account to lock, specific password to rotate.
- Deduplicate: merge two findings that point to the same action; keep the higher risk_level.
- Sort by descending severity.
- INVALID example: "Improve your privacy" — must be specific."""


def _build_prompt(findings: list[dict], ig_user: dict | None) -> str:
    return (
        f"Instagram profile snapshot:\n{json.dumps(ig_user or {}, indent=2)}\n\n"
        f"Pipeline findings ({len(findings)} total):\n{json.dumps(findings, indent=2)}\n\n"
        "Produce the JSON risk assessment now."
    )


async def run_aggregator(session: SessionState) -> None:
    settings = get_settings()
    findings: list[dict] = session.data.get("all_findings", [])
    ig_user: dict | None = session.data.get("ig_user")

    if not settings.anthropic_api_key:
        await emit_aggregator_done(session, {
            "exposure_score": 0,
            "summary": "Aggregator skipped: ANTHROPIC_API_KEY not configured.",
            "remediation_list": [],
        })
        return

    payload = {
        "model": AGGREGATION_MODEL,
        "max_tokens": 4096,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_prompt(findings, ig_user)}],
    }

    try:
        async with ANTHROPIC_SEMAPHORE:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    str(settings.anthropic_api_url),
                    headers={
                        "x-api-key": settings.anthropic_api_key,
                        "anthropic-version": settings.anthropic_version,
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

        usage = data.get("usage", {})
        cost = (
            usage.get("input_tokens", 0) * settings.claude_sonnet_input_cost_per_million / 1_000_000
            + usage.get("output_tokens", 0) * settings.claude_sonnet_output_cost_per_million / 1_000_000
        )
        new_total, _ = await add_cost(session, cost)
        await emit_cost_update(session, new_total)

        result = json.loads(data["content"][0]["text"])
        await emit_aggregator_done(session, result)

    except Exception as exc:
        await emit_aggregator_done(session, {
            "exposure_score": 0,
            "summary": f"Aggregator error: {exc}",
            "remediation_list": [],
            "error": str(exc),
        })
