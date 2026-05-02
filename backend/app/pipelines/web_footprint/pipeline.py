from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura

from app.core.config import Settings
from app.core.session_store import SessionState

LOGGER = logging.getLogger(__name__)

PIPELINE_NAME = "web_footprint"
PIPELINE_TYPE = "web_appearance"
DORK_MODEL = "claude-sonnet-4-6"
TRIAGE_MODEL = "claude-haiku-4-5-20251001"
TERMINAL_PIPELINE_STATUSES = {"completed", "budget_exceeded", "error"}


class BudgetExceededError(RuntimeError):
    """Raised when the pipeline cannot spend additional budget."""


@dataclass(slots=True)
class SearchResult:
    query: str
    url: str
    title: str
    snippet: str


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = _normalize_whitespace(value)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _platform_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "reddit.com" in host:
        return "reddit"
    if "github.com" in host:
        return "github"
    if "pastebin.com" in host:
        return "pastebin"
    if host.endswith(".edu") or ".edu" in host:
        return "edu"
    if host.startswith("www."):
        host = host[4:]
    return host or "web"


def _risk_from_label(label: str, severity: str) -> str:
    normalized = severity.strip().upper()
    if normalized in {"LOW", "MEDIUM", "HIGH"}:
        return normalized
    if label == "location_revealing":
        return "MEDIUM"
    if label == "identity_revealing":
        return "HIGH"
    return "LOW"


def _extract_json_fragment(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)

    object_start = stripped.find("{")
    array_start = stripped.find("[")

    if array_start != -1 and (object_start == -1 or array_start < object_start):
        return stripped[array_start : stripped.rfind("]") + 1]
    if object_start != -1:
        return stripped[object_start : stripped.rfind("}") + 1]
    return stripped


def _extract_excerpt(text: str, needles: list[str], context_window: int = 200) -> str:
    cleaned = _normalize_whitespace(text)
    if not cleaned:
        return ""

    paragraphs = [segment.strip() for segment in re.split(r"\n{2,}", text) if segment.strip()]
    lowered_needles = [needle.casefold() for needle in needles if needle]

    for paragraph in paragraphs:
        lowered = paragraph.casefold()
        for needle in lowered_needles:
            index = lowered.find(needle)
            if index == -1:
                continue
            start = max(0, index - context_window)
            end = min(len(paragraph), index + len(needle) + context_window)
            return _normalize_whitespace(paragraph[start:end])

    lowered_text = cleaned.casefold()
    for needle in lowered_needles:
        index = lowered_text.find(needle)
        if index == -1:
            continue
        start = max(0, index - context_window)
        end = min(len(cleaned), index + len(needle) + context_window)
        return _normalize_whitespace(cleaned[start:end])

    return cleaned[: context_window * 2]


async def emit_pipeline_status(
    session: SessionState,
    status: str,
    **payload: Any,
) -> None:
    async with session.state_lock:
        pipeline_statuses = session.data.setdefault("pipeline_statuses", {})
        pipeline_statuses[PIPELINE_NAME] = status

    event = {
        "pipeline": PIPELINE_NAME,
        "type": "pipeline_status",
        "pipeline_status": status,
        "timestamp": utcnow_iso(),
        **payload,
    }
    await session.publish_event(event)


async def emit_finding(session: SessionState, finding: dict[str, Any]) -> None:
    finding_event = {
        "pipeline": PIPELINE_NAME,
        "type": PIPELINE_TYPE,
        "timestamp": utcnow_iso(),
        **finding,
    }
    async with session.state_lock:
        findings = session.data.setdefault("findings", [])
        findings.append(finding_event)
    await session.publish_event(finding_event)


async def get_pipeline_status(session: SessionState) -> str | None:
    async with session.state_lock:
        return session.data.get("pipeline_statuses", {}).get(PIPELINE_NAME)


async def get_web_context(session: SessionState) -> dict[str, Any]:
    async with session.state_lock:
        data = dict(session.data)
        session_ig_user = dict(session.ig_user or {})

    ig_user = data.get("ig_user") if isinstance(data.get("ig_user"), dict) else session_ig_user
    identity = data.get("identity_context") if isinstance(data.get("identity_context"), dict) else {}
    geolocation = data.get("geolocation_context") if isinstance(data.get("geolocation_context"), dict) else {}

    handle = _first_non_empty(
        data.get("handle"),
        ig_user.get("username"),
        data.get("instagram_handle"),
        identity.get("handle"),
    )
    real_name = _first_non_empty(
        data.get("real_name"),
        ig_user.get("name"),
        identity.get("real_name"),
        identity.get("full_name"),
        identity.get("name"),
    )
    suspected_region = _first_non_empty(
        data.get("suspected_region"),
        geolocation.get("suspected_region"),
        geolocation.get("region"),
        geolocation.get("likely_region"),
    )

    emails = _dedupe_preserve_order(
        _listify(data.get("emails"))
        + _listify(identity.get("emails"))
        + _listify(ig_user.get("email"))
    )

    return {
        "handle": handle or "",
        "real_name": real_name or "",
        "emails": emails,
        "suspected_region": suspected_region or "",
    }


def build_fallback_queries(context: dict[str, Any]) -> list[str]:
    handle = context.get("handle", "").strip()
    real_name = context.get("real_name", "").strip()
    suspected_region = context.get("suspected_region", "").strip()
    emails = context.get("emails", [])

    queries = [
        f'"{handle}"',
        f'"@{handle}"',
        f'site:reddit.com "{handle}"',
        f'site:github.com "{handle}"',
        f'site:pastebin.com "{handle}"',
        f'site:.edu "{handle}"',
        f'"{handle}" (comment OR profile OR bio OR meetup)',
        f'"{handle}" (gym OR barber OR school OR campus OR office)',
        f'"{handle}" (Reddit OR GitHub OR Pastebin)',
        f'"{handle}" (friend OR roommate OR coworker OR classmate)',
        f'"{handle}" (coffee OR diner OR brunch OR studio)',
        f'"{handle}" (city OR neighborhood OR commute OR local)',
    ]

    if real_name:
        queries.extend(
            [
                f'"{real_name}" "{handle}"',
                f'"{real_name}" site:reddit.com',
                f'"{real_name}" site:github.com',
            ]
        )
    if suspected_region:
        queries.extend(
            [
                f'"{handle}" "{suspected_region}"',
                f'"{real_name or handle}" "{suspected_region}" site:reddit.com',
            ]
        )
    for email in emails[:2]:
        queries.append(f'"{email}"')

    return _dedupe_preserve_order(queries)


def build_enrichment_queries(context: dict[str, Any]) -> list[str]:
    handle = context.get("handle", "").strip()
    real_name = context.get("real_name", "").strip()
    suspected_region = context.get("suspected_region", "").strip()
    emails = context.get("emails", [])

    supplemental: list[str] = []
    if real_name and handle:
        supplemental.append(f'"{real_name}" "@{handle}"')
    if suspected_region and handle:
        supplemental.append(f'site:reddit.com "{handle}" "{suspected_region}"')
        supplemental.append(f'"{handle}" "{suspected_region}" (gym OR school OR work)')
    if real_name and suspected_region:
        supplemental.append(f'"{real_name}" "{suspected_region}" (school OR employer OR campus)')
    for email in emails[:2]:
        supplemental.append(f'"{email}" "{handle}"')
    return _dedupe_preserve_order(supplemental)


class AnthropicClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http_client = http_client

    def _pricing(self, model: str) -> tuple[float, float]:
        if model == self._settings.anthropic_sonnet_model:
            return (
                self._settings.claude_sonnet_input_cost_per_million,
                self._settings.claude_sonnet_output_cost_per_million,
            )
        return (
            self._settings.claude_haiku_input_cost_per_million,
            self._settings.claude_haiku_output_cost_per_million,
        )

    def _estimate_cost(self, model: str, prompt: str, max_output_tokens: int) -> float:
        input_rate, output_rate = self._pricing(model)
        estimated_input_tokens = max(1, math.ceil(len(prompt) / 4))
        return (estimated_input_tokens * input_rate / 1_000_000) + (
            max_output_tokens * output_rate / 1_000_000
        )

    async def _ensure_budget(
        self,
        session: SessionState,
        model: str,
        prompt: str,
        max_output_tokens: int,
    ) -> None:
        estimated_cost = self._estimate_cost(model, prompt, max_output_tokens)
        async with session.state_lock:
            cost_tracking = session.data.setdefault(
                "cost_tracking",
                {"cumulative_usd": 0.0, "pipelines": {}},
            )
            cumulative = float(cost_tracking.get("cumulative_usd", 0.0))
            pipeline_cost = float(cost_tracking["pipelines"].get(PIPELINE_NAME, 0.0))

        remaining_total = self._settings.audit_cost_ceiling_usd - cumulative
        remaining_pipeline = self._settings.web_footprint_budget_share_usd - pipeline_cost
        if estimated_cost > min(remaining_total, remaining_pipeline):
            await emit_pipeline_status(
                session,
                "budget_exceeded",
                estimated_cost_usd=round(estimated_cost, 6),
                remaining_total_usd=round(max(remaining_total, 0.0), 6),
                remaining_pipeline_usd=round(max(remaining_pipeline, 0.0), 6),
            )
            raise BudgetExceededError("Web footprint budget exhausted")

    async def _record_cost(
        self,
        session: SessionState,
        model: str,
        usage: dict[str, Any],
    ) -> None:
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        input_rate, output_rate = self._pricing(model)
        cost = (input_tokens * input_rate / 1_000_000) + (output_tokens * output_rate / 1_000_000)

        async with session.state_lock:
            cost_tracking = session.data.setdefault(
                "cost_tracking",
                {"cumulative_usd": 0.0, "pipelines": {}},
            )
            cost_tracking["cumulative_usd"] = round(
                float(cost_tracking.get("cumulative_usd", 0.0)) + cost,
                6,
            )
            pipelines = cost_tracking.setdefault("pipelines", {})
            pipelines[PIPELINE_NAME] = round(float(pipelines.get(PIPELINE_NAME, 0.0)) + cost, 6)
            session.data["cumulative_cost_usd"] = cost_tracking["cumulative_usd"]

            cumulative = float(cost_tracking["cumulative_usd"])
            pipeline_cost = float(pipelines[PIPELINE_NAME])

        if (
            cumulative >= self._settings.audit_cost_ceiling_usd
            or pipeline_cost >= self._settings.web_footprint_budget_share_usd
        ):
            await emit_pipeline_status(
                session,
                "budget_exceeded",
                cumulative_cost_usd=round(cumulative, 6),
                pipeline_cost_usd=round(pipeline_cost, 6),
            )
            raise BudgetExceededError("Web footprint budget exhausted")

    async def message_json(
        self,
        session: SessionState,
        *,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
    ) -> Any:
        if not self._settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        prompt = f"{system}\n\n{user}"
        await self._ensure_budget(session, model, prompt, max_output_tokens)

        response = await self._http_client.post(
            str(self._settings.anthropic_api_url),
            headers={
                "x-api-key": self._settings.anthropic_api_key,
                "anthropic-version": self._settings.anthropic_version,
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_output_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        response.raise_for_status()
        payload = response.json()
        await self._record_cost(session, model, payload.get("usage", {}))

        text = "".join(
            part.get("text", "")
            for part in payload.get("content", [])
            if isinstance(part, dict) and part.get("type") == "text"
        )
        return json.loads(_extract_json_fragment(text))


class GoogleCustomSearchClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http_client = http_client

    async def _rate_limit(self, session: SessionState) -> None:
        loop = asyncio.get_running_loop()
        while True:
            async with session.state_lock:
                last_request = float(session.data.get("google_cse_last_request_monotonic", 0.0))
                now = loop.time()
                wait_seconds = max(0.0, 1.0 - (now - last_request))
                if wait_seconds == 0.0:
                    session.data["google_cse_last_request_monotonic"] = now
                    return
            await asyncio.sleep(wait_seconds)

    async def search(self, session: SessionState, query: str) -> list[SearchResult]:
        if not self._settings.google_cse_api_key or not self._settings.google_cse_cx:
            raise RuntimeError("Google Custom Search credentials are not configured")

        cache_key = hashlib.sha256(query.encode("utf-8")).hexdigest()
        async with session.state_lock:
            cache = session.data.setdefault("google_cse_cache", {})
            cached = cache.get(cache_key)
            if isinstance(cached, dict) and "results" in cached:
                return [
                    SearchResult(
                        query=query,
                        url=item["url"],
                        title=item["title"],
                        snippet=item["snippet"],
                    )
                    for item in cached["results"]
                    if isinstance(item, dict)
                ]

        backoff_seconds = 1.0
        for attempt in range(3):
            await self._rate_limit(session)
            response = await self._http_client.get(
                str(self._settings.google_cse_api_url),
                params={
                    "key": self._settings.google_cse_api_key,
                    "cx": self._settings.google_cse_cx,
                    "q": query,
                    "num": self._settings.web_footprint_queries_per_call,
                },
            )

            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(backoff_seconds)
                backoff_seconds *= 2
                continue

            response.raise_for_status()
            payload = response.json()
            items = payload.get("items", [])
            results = [
                SearchResult(
                    query=query,
                    url=item.get("link", ""),
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                )
                for item in items[:5]
                if isinstance(item, dict) and item.get("link")
            ]

            async with session.state_lock:
                cache = session.data.setdefault("google_cse_cache", {})
                cache[cache_key] = {
                    "query": query,
                    "results": [
                        {"url": result.url, "title": result.title, "snippet": result.snippet}
                        for result in results
                    ],
                }
            return results

        return []


async def generate_queries(
    session: SessionState,
    anthropic: AnthropicClient,
    context: dict[str, Any],
    settings: Settings,
) -> list[str]:
    fallback_queries = build_fallback_queries(context)

    system = (
        "You generate Google dork queries for consent-based self-audits. "
        "Return strict JSON arrays only."
    )
    user = (
        "Given this context about an Instagram user, generate 12 to 15 Google dork queries "
        "to find public web appearances of this person, particularly comments revealing their "
        "location, employer, school, barber, gym, friends, or regular spots. Include "
        "platform-specific queries for Reddit, GitHub, Pastebin, and .edu domains. "
        "Return a JSON array of query strings only, no preamble.\n\n"
        f"{json.dumps(context, ensure_ascii=True)}"
    )

    try:
        generated = await anthropic.message_json(
            session,
            model=settings.anthropic_sonnet_model,
            system=system,
            user=user,
            max_output_tokens=600,
        )
        if not isinstance(generated, list):
            raise ValueError("Dork generation did not return a JSON array")
        llm_queries = [str(item) for item in generated if isinstance(item, str)]
    except BudgetExceededError:
        raise
    except Exception as exc:
        LOGGER.warning("Falling back to heuristic web-footprint queries: %s", exc)
        llm_queries = fallback_queries

    freshest_context = await get_web_context(session)
    combined = _dedupe_preserve_order(
        llm_queries + build_enrichment_queries(freshest_context) + fallback_queries
    )
    return combined[: settings.web_footprint_max_queries]


async def triage_result(
    session: SessionState,
    anthropic: AnthropicClient,
    context: dict[str, Any],
    result: SearchResult,
    settings: Settings,
) -> dict[str, Any]:
    system = (
        "You classify search result snippets for a consent-based self-audit. "
        "Return strict JSON only."
    )
    user = (
        "Classify this search result snippet into one label: "
        "location_revealing, identity_revealing, benign, already_known. "
        "Use already_known only when the snippet adds no new linkage beyond the known context. "
        "Return JSON with keys label, confidence, and rationale.\n\n"
        f"Known context: {json.dumps(context, ensure_ascii=True)}\n"
        f"Query: {result.query}\n"
        f"Title: {result.title}\n"
        f"URL: {result.url}\n"
        f"Snippet: {result.snippet}"
    )
    payload = await anthropic.message_json(
        session,
        model=settings.anthropic_haiku_model,
        system=system,
        user=user,
        max_output_tokens=200,
    )

    label = str(payload.get("label", "benign")).strip().lower()
    if label not in {"location_revealing", "identity_revealing", "benign", "already_known"}:
        label = "benign"

    confidence = float(payload.get("confidence", 0.5) or 0.5)
    return {
        "label": label,
        "confidence": max(0.0, min(confidence, 1.0)),
        "rationale": str(payload.get("rationale", "")).strip(),
    }


async def fetch_page_excerpt(
    http_client: httpx.AsyncClient,
    result: SearchResult,
    context: dict[str, Any],
) -> str:
    response = await http_client.get(result.url, follow_redirects=True)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if not any(token in content_type for token in ("text/html", "text/plain", "application/xhtml+xml")):
        return ""

    extracted = trafilatura.extract(response.text, include_comments=False, include_tables=False) or ""
    needles = _dedupe_preserve_order(
        [
            context.get("handle", ""),
            f"@{context.get('handle', '')}" if context.get("handle") else "",
            context.get("real_name", ""),
            *context.get("emails", []),
        ]
    )
    return _extract_excerpt(extracted, needles)


async def analyze_excerpt(
    session: SessionState,
    anthropic: AnthropicClient,
    context: dict[str, Any],
    result: SearchResult,
    excerpt: str,
    settings: Settings,
) -> dict[str, Any]:
    system = (
        "You assess public web mentions for a consent-based self-audit. "
        "Return strict JSON only."
    )
    user = (
        "Given this public page excerpt about the user, return JSON with keys: "
        "label, severity, confidence, remediation, summary. "
        "Valid labels: location_revealing, identity_revealing, benign, already_known. "
        "Severity must be LOW, MEDIUM, or HIGH. "
        "Remediation must be concrete and actionable for the person who posted it.\n\n"
        f"Known context: {json.dumps(context, ensure_ascii=True)}\n"
        f"URL: {result.url}\n"
        f"Title: {result.title}\n"
        f"Search snippet: {result.snippet}\n"
        f"Excerpt: {excerpt}"
    )
    payload = await anthropic.message_json(
        session,
        model=settings.anthropic_haiku_model,
        system=system,
        user=user,
        max_output_tokens=260,
    )
    return {
        "label": str(payload.get("label", "benign")).strip().lower(),
        "severity": str(payload.get("severity", "LOW")).strip().upper(),
        "confidence": float(payload.get("confidence", 0.5) or 0.5),
        "remediation": _normalize_whitespace(str(payload.get("remediation", "")).strip()),
        "summary": _normalize_whitespace(str(payload.get("summary", "")).strip()),
    }


async def run_web_footprint_pipeline(
    session: SessionState,
    settings: Settings,
) -> None:
    http_timeout = httpx.Timeout(20.0, connect=10.0)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

    try:
        context = await get_web_context(session)
        handle = context.get("handle", "").strip()
        if not handle:
            await emit_pipeline_status(
                session,
                "error",
                detail="No Instagram handle found in the session context.",
            )
            return

        await emit_pipeline_status(session, "started", handle=handle)
        await emit_pipeline_status(session, "generating_queries")

        async with httpx.AsyncClient(timeout=http_timeout, limits=limits, headers={"user-agent": "ShieldClaw/1.0"}) as http_client:
            anthropic = AnthropicClient(settings, http_client)
            search = GoogleCustomSearchClient(settings, http_client)

            queries = await generate_queries(session, anthropic, context, settings)
            await emit_pipeline_status(
                session,
                "searching",
                query_count=len(queries),
            )

            seen_urls: set[str] = set()
            full_fetches = 0

            for query_index, query in enumerate(queries, start=1):
                await emit_pipeline_status(
                    session,
                    "search_query_started",
                    query=query,
                    query_index=query_index,
                )

                try:
                    results = await search.search(session, query)
                except Exception as exc:
                    LOGGER.warning("Google search failed for query %r: %s", query, exc)
                    await emit_pipeline_status(
                        session,
                        "search_query_failed",
                        query=query,
                        query_index=query_index,
                        detail=str(exc),
                    )
                    continue

                for result in results:
                    if result.url in seen_urls:
                        continue
                    seen_urls.add(result.url)

                    try:
                        triage = await triage_result(session, anthropic, context, result, settings)
                    except BudgetExceededError:
                        return
                    except Exception as exc:
                        LOGGER.warning("Snippet triage failed for %s: %s", result.url, exc)
                        continue

                    label = triage["label"]
                    if label not in {"location_revealing", "identity_revealing"}:
                        continue
                    if full_fetches >= settings.web_footprint_max_full_fetches:
                        await emit_pipeline_status(
                            session,
                            "full_fetch_cap_reached",
                            max_full_fetches=settings.web_footprint_max_full_fetches,
                        )
                        break

                    try:
                        excerpt = await fetch_page_excerpt(http_client, result, context)
                    except Exception as exc:
                        LOGGER.warning("Full-page fetch failed for %s: %s", result.url, exc)
                        continue

                    if not excerpt:
                        continue

                    full_fetches += 1
                    try:
                        analysis = await analyze_excerpt(
                            session,
                            anthropic,
                            context,
                            result,
                            excerpt,
                            settings,
                        )
                    except BudgetExceededError:
                        return
                    except Exception as exc:
                        LOGGER.warning("Excerpt analysis failed for %s: %s", result.url, exc)
                        continue

                    final_label = analysis["label"]
                    if final_label not in {"location_revealing", "identity_revealing"}:
                        continue

                    await emit_finding(
                        session,
                        {
                            "url": result.url,
                            "platform": _platform_from_url(result.url),
                            "snippet": excerpt,
                            "category": final_label,
                            "confidence": round(float(analysis["confidence"]), 3),
                            "risk_level": _risk_from_label(final_label, analysis["severity"]),
                            "remediation": analysis["remediation"],
                            "title": result.title,
                            "query": result.query,
                            "summary": analysis["summary"],
                        },
                    )

            await emit_pipeline_status(session, "completed", full_fetches=full_fetches)
    except BudgetExceededError:
        return
    except Exception as exc:
        LOGGER.exception("Web footprint pipeline failed")
        await emit_pipeline_status(session, "error", detail=str(exc))
    finally:
        current_task = asyncio.current_task()
        async with session.state_lock:
            task = session.audit_tasks.get(PIPELINE_NAME)
            if task is current_task:
                session.audit_tasks.pop(PIPELINE_NAME, None)


async def ensure_web_footprint_audit_task(
    session: SessionState,
    settings: Settings,
) -> asyncio.Task[None]:
    async with session.state_lock:
        existing_task = session.audit_tasks.get(PIPELINE_NAME)
        if existing_task and not existing_task.done():
            return existing_task

        current_status = session.data.get("pipeline_statuses", {}).get(PIPELINE_NAME)
        if current_status in TERMINAL_PIPELINE_STATUSES:
            completed_task: asyncio.Task[None] = asyncio.get_running_loop().create_task(asyncio.sleep(0))
            return completed_task

        task = asyncio.create_task(run_web_footprint_pipeline(session, settings))
        session.audit_tasks[PIPELINE_NAME] = task
        return task
