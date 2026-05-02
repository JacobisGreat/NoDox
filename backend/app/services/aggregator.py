"""Final-pass aggregator that turns raw findings into a risk report.

Run after every pipeline finishes (and only if at least one finding has
been emitted). Produces the ``aggregator_done`` event consumed by the
frontend dashboard.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.schemas.events import aggregator_done as _aggregator_done_event
from app.schemas.events import cost_update as _cost_update_event
from app.session_store import SessionState

_SYSTEM_PROMPT = """\
You are a privacy risk analyst for NODOXX, a consent-based Instagram OSINT
self-audit tool. The user has audited their own public presence. Produce a
structured, actionable risk assessment they can act on within 30 minutes.

Respond with valid JSON only — no prose, no markdown fences.

Output schema:
{
  "exposure_score": <integer 0-100>,
  "summary": "<EXACTLY two sentences, screenshot-ready>",
  "remediation_list": [
    {
      "priority": <integer starting at 1>,
      "action": "<concrete action that names a specific URL, settings path, or account>",
      "reason": "<one sentence citing the specific evidence>",
      "risk_level": "<HIGH|MEDIUM|LOW>"
    }
  ]
}

Exposure score weighting (sum of weighted sub-scores, clamped 0-100):
- location pinpointing findings: 0.40
- identity cross-reference findings: 0.35
- web footprint findings: 0.15
- weak / low-confidence signals: 0.10

Remediation rules:
- Every action MUST name a concrete surface: a specific post URL to delete,
  a specific settings path to change, a specific account to lock, a specific
  password to rotate. "Improve your privacy" is INVALID.
- Deduplicate: merge two findings that point to the same action; keep the
  higher risk_level.
- Sort the list so priority 1 is the single highest-impact step."""


def _group_by_pipeline(findings: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {
        "identity": [],
        "geolocation": [],
        "web_footprint": [],
        "other": [],
    }
    for f in findings:
        bucket = f.get("_pipeline") or f.get("pipeline") or "other"
        if bucket not in grouped:
            bucket = "other"
        grouped[bucket].append(f)
    return grouped


def _build_user_prompt(
    profile: dict | None, grouped: dict[str, list[dict]]
) -> str:
    parts: list[str] = []
    parts.append("Instagram profile snapshot:")
    parts.append(json.dumps(profile or {}, indent=2, ensure_ascii=False))
    parts.append("")
    for pipeline, items in grouped.items():
        parts.append(f"Findings from pipeline '{pipeline}' ({len(items)}):")
        if not items:
            parts.append("  (none)")
        else:
            parts.append(json.dumps(items, indent=2, ensure_ascii=False))
        parts.append("")
    parts.append("Produce the JSON risk assessment now.")
    return "\n".join(parts)


def _fallback_report(findings: list[dict], reason: str) -> dict[str, Any]:
    if not findings:
        return {
            "exposure_score": 0,
            "summary": (
                "No risk signals were collected during this audit. "
                f"Aggregator note: {reason}"
            ),
            "remediation_list": [],
        }
    seen: dict[str, dict[str, Any]] = {}
    rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    for f in findings:
        action = f.get("remediation") or "Review and remove the linked surface"
        key = action.strip().lower()
        risk = (f.get("risk_level") or "LOW").upper()
        existing = seen.get(key)
        if existing is None or rank.get(risk, 0) > rank.get(existing["risk_level"], 0):
            seen[key] = {
                "action": action,
                "reason": (f.get("evidence_chain") or [""])[0] or "See finding evidence.",
                "risk_level": risk,
            }
    items = sorted(
        seen.values(),
        key=lambda x: rank.get(x["risk_level"], 0),
        reverse=True,
    )
    remediation_list = [
        {
            "priority": idx + 1,
            "action": item["action"],
            "reason": item["reason"],
            "risk_level": item["risk_level"],
        }
        for idx, item in enumerate(items[:15])
    ]
    high_count = sum(1 for f in findings if (f.get("risk_level") or "").upper() in {"HIGH", "CRITICAL"})
    score = min(100, 25 + 5 * len(findings) + 8 * high_count)
    return {
        "exposure_score": int(score),
        "summary": (
            f"Collected {len(findings)} risk signal(s) across the pipelines, "
            f"including {high_count} high-severity item(s). "
            f"Aggregator note: {reason}"
        ),
        "remediation_list": remediation_list,
    }


def _coerce_report(raw: str, findings: list[dict]) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Strip code fences if the model added them despite instructions.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if "\n" in cleaned:
                cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return _fallback_report(findings, "model response was not valid JSON")
    if not isinstance(parsed, dict):
        return _fallback_report(findings, "model response was not a JSON object")
    score = parsed.get("exposure_score", 0)
    try:
        score_int = int(score)
    except (TypeError, ValueError):
        score_int = 0
    score_int = max(0, min(100, score_int))
    summary = str(parsed.get("summary", "")).strip() or "Audit complete."
    raw_list = parsed.get("remediation_list", []) or []
    remediation_list: list[dict[str, Any]] = []
    if isinstance(raw_list, list):
        for idx, item in enumerate(raw_list):
            if not isinstance(item, dict):
                continue
            try:
                priority = int(item.get("priority", idx + 1))
            except (TypeError, ValueError):
                priority = idx + 1
            remediation_list.append(
                {
                    "priority": priority,
                    "action": str(item.get("action", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                    "risk_level": str(item.get("risk_level", "LOW"))
                    .strip()
                    .upper(),
                }
            )
    return {
        "exposure_score": score_int,
        "summary": summary,
        "remediation_list": remediation_list,
    }


async def run(session: SessionState) -> None:
    settings = get_settings()
    findings: list[dict] = list(session.data.get("findings", []))
    profile: dict | None = session.data.get("profile")

    if not findings:
        # Caller checked, but be defensive.
        report = _fallback_report(findings, "no findings emitted")
        await session.publish_event(
            _aggregator_done_event(
                report["exposure_score"], report["summary"], report["remediation_list"]
            )
        )
        return

    if not settings.gemini_api_key:
        report = _fallback_report(findings, "GEMINI_API_KEY not configured")
        await session.publish_event(
            _aggregator_done_event(
                report["exposure_score"], report["summary"], report["remediation_list"]
            )
        )
        return

    cost_tracker = session.data.get("cost_tracker")
    anthropic = session.data.get("anthropic")
    if anthropic is None:
        report = _fallback_report(findings, "Gemini client unavailable")
        await session.publish_event(
            _aggregator_done_event(
                report["exposure_score"], report["summary"], report["remediation_list"]
            )
        )
        return

    # Soft check — the aggregator is small but we still respect the ceiling.
    estimated = 0.05  # USD; one Sonnet call with ~3-4k input + ~1k output
    if cost_tracker is not None and not cost_tracker.can_spend(estimated):
        report = _fallback_report(findings, "global budget exhausted")
        await session.publish_event(
            _aggregator_done_event(
                report["exposure_score"], report["summary"], report["remediation_list"]
            )
        )
        return

    grouped = _group_by_pipeline(findings)
    user_prompt = _build_user_prompt(profile, grouped)

    sem = session.data.get("anthropic_semaphore")
    try:
        if sem is not None:
            async with sem:
                text, usage = await anthropic.call_text(
                    model=settings.gemini_pro_model,
                    system=_SYSTEM_PROMPT,
                    user_content=user_prompt,
                    max_tokens=4096,
                    response_json=True,
                )
        else:
            text, usage = await anthropic.call_text(
                model=settings.gemini_pro_model,
                system=_SYSTEM_PROMPT,
                user_content=user_prompt,
                max_tokens=4096,
                response_json=True,
            )
    except Exception as exc:
        report = _fallback_report(findings, f"aggregator call failed: {exc}")
        await session.publish_event(
            _aggregator_done_event(
                report["exposure_score"], report["summary"], report["remediation_list"]
            )
        )
        return

    if cost_tracker is not None:
        cost_tracker.record_anthropic(usage, scope="aggregator")
        await session.publish_event(_cost_update_event(cost_tracker.total_usd))

    report = _coerce_report(text, findings)
    await session.publish_event(
        _aggregator_done_event(
            report["exposure_score"],
            report["summary"],
            report["remediation_list"],
        )
    )
