"""Web-footprint / dorking pipeline.

Runs a small bundle of Google CSE queries against the user's profile
metadata to surface mentions, leaks, and side-channel PII outside of
Instagram itself. Each result that survives a Haiku relevance triage is
fully fetched (trafilatura), re-scanned by Haiku for structured PII, and
the extracted items are emitted as findings.

Budget is owned by the ``web_footprint`` cost-tracker scope. Whenever a
spend would push the scope or the global ceiling over budget the pipeline
emits ``budget_exceeded`` and stops gracefully.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from app.config import get_settings
from app.schemas.events import (
    cost_update as _cost_update_event,
    finding as _finding_event,
    pipeline_status as _pipeline_status_event,
)
from app.schemas.findings import Finding
from app.services import account_probe as _account_probe
from app.services import breach_check as _breach_check
from app.services import dork_catalog as _dork_catalog
from app.services import intelbase as _intelbase
from app.services.serper import SerperClient, SerperError
from app.services.trafilatura_fetch import TrafilaturaFetcher
from app.session_store import SessionState


_PIPELINE = "web_footprint"
_SCOPE = "web_footprint"

# Conservative budget guesses (input + output token cost) used to gate
# can_spend() before each call. Real cost is recorded after.
_HAIKU_TRIAGE_COST_GUESS = 0.003
_HAIKU_EXTRACT_COST_GUESS = 0.008
_SONNET_QUERY_GEN_COST_GUESS = 0.015


_QUERY_GEN_SYSTEM_PROMPT = (
    "You are an OSINT query generator. Given a target's Instagram profile, "
    "generate 12-15 Google search queries that could reveal additional "
    "personal information. Focus on: email addresses, phone numbers, real "
    "name, workplace, school, home address, daily routines, social circles. "
    "Each query should use Google dork syntax. Respond with JSON: "
    "{\"queries\": [str]}"
)

_TRIAGE_SYSTEM_PROMPT = (
    "You are an OSINT relevance classifier. Given a search result about a "
    "target person, determine if it contains personally identifiable "
    "information or is likely about the target. Respond with JSON: "
    "{\"relevant\": bool, \"relevance_reason\": str, \"pii_types\": [str]}"
)


def _extract_system_prompt(username: str, full_name: str) -> str:
    return (
        "You are an OSINT extractor. Given a web page about a target person "
        f"(username: {username}, name: {full_name}), extract all personally "
        "identifiable information. Respond with JSON: {\"findings\": "
        "[{\"type\": str, \"value\": str, \"context\": str, "
        "\"confidence\": float}]}"
    )


# ---------------------------------------------------------------------------
# Query generation.
# ---------------------------------------------------------------------------


def _base_queries(username: str, full_name: str) -> list[str]:
    queries: list[str] = []
    if username:
        queries.append(f"\"{username}\"")
        queries.append(f"site:reddit.com \"{username}\"")
        queries.append(f"site:github.com \"{username}\"")
        queries.append(f"site:pastebin.com \"{username}\"")
        queries.append(
            f"\"{username}\" (gym OR barber OR school OR campus OR office)"
        )
        queries.append(
            f"\"{username}\" (friend OR roommate OR coworker OR classmate)"
        )
    if full_name:
        if username:
            queries.append(f"\"{full_name}\" \"{username}\"")
        queries.append(f"site:*.edu \"{full_name}\"")
    # DorkER-derived patterns. De-duplicated via the set in run().
    queries.extend(_dork_catalog.dorks_for_username(username))
    queries.extend(_dork_catalog.dorks_for_full_name(full_name))
    return queries


def _is_email_value(pii_type: str) -> bool:
    return "email" in (pii_type or "").lower()


async def _generate_custom_queries(
    session: SessionState, profile: dict, max_count: int
) -> list[str]:
    if max_count <= 0:
        return []
    settings = get_settings()
    cost_tracker = session.data.get("cost_tracker")
    anthropic = session.data.get("anthropic")
    if anthropic is None or not settings.anthropic_api_key:
        return []
    if cost_tracker is not None and not cost_tracker.can_spend(
        _SONNET_QUERY_GEN_COST_GUESS, scope=_SCOPE
    ):
        return []

    profile_brief = {
        "username": profile.get("username", ""),
        "full_name": profile.get("full_name", ""),
        "biography": profile.get("biography", ""),
        "external_url": profile.get("external_url"),
    }
    user_prompt = (
        f"Target Instagram profile (generate up to {max_count} queries):\n"
        + json.dumps(profile_brief, indent=2, ensure_ascii=False)
    )

    try:
        text, usage = await anthropic.call_text(
            model=settings.anthropic_sonnet_model,
            system=_QUERY_GEN_SYSTEM_PROMPT,
            user_content=user_prompt,
            max_tokens=1200,
            response_json=True,
        )
    except Exception:
        return []

    if cost_tracker is not None:
        cost_tracker.record_anthropic(usage, scope=_SCOPE)
        await session.publish_event(_cost_update_event(cost_tracker.total_usd))

    parsed = _parse_json_response(text)
    if parsed is None:
        return []
    raw_queries = parsed.get("queries") or []
    if not isinstance(raw_queries, list):
        return []
    custom: list[str] = []
    for q in raw_queries:
        s = str(q or "").strip()
        if s:
            custom.append(s)
        if len(custom) >= max_count:
            break
    return custom


# ---------------------------------------------------------------------------
# Triage and extraction.
# ---------------------------------------------------------------------------


async def _triage_result(
    session: SessionState, result: dict
) -> dict | None:
    settings = get_settings()
    cost_tracker = session.data.get("cost_tracker")
    anthropic = session.data.get("anthropic")
    if anthropic is None or not settings.anthropic_api_key:
        return None
    if cost_tracker is not None and not cost_tracker.can_spend(
        _HAIKU_TRIAGE_COST_GUESS, scope=_SCOPE
    ):
        return None

    payload = {
        "title": result.get("title", ""),
        "url": result.get("link", ""),
        "snippet": result.get("snippet", ""),
        "displayLink": result.get("displayLink", ""),
    }
    try:
        text, usage = await anthropic.call_text(
            model=settings.anthropic_haiku_model,
            system=_TRIAGE_SYSTEM_PROMPT,
            user_content=json.dumps(payload, ensure_ascii=False),
            max_tokens=400,
            response_json=True,
        )
    except Exception:
        return None

    if cost_tracker is not None:
        cost_tracker.record_anthropic(usage, scope=_SCOPE)
        await session.publish_event(_cost_update_event(cost_tracker.total_usd))

    parsed = _parse_json_response(text)
    if parsed is None:
        return None
    return parsed


async def _extract_pii(
    session: SessionState,
    page_text: str,
    profile: dict,
) -> list[dict]:
    settings = get_settings()
    cost_tracker = session.data.get("cost_tracker")
    anthropic = session.data.get("anthropic")
    if anthropic is None or not settings.anthropic_api_key:
        return []
    if cost_tracker is not None and not cost_tracker.can_spend(
        _HAIKU_EXTRACT_COST_GUESS, scope=_SCOPE
    ):
        return []

    username = str(profile.get("username", "") or "")
    full_name = str(profile.get("full_name", "") or "")

    try:
        text, usage = await anthropic.call_text(
            model=settings.anthropic_haiku_model,
            system=_extract_system_prompt(username, full_name),
            user_content=f"Page content:\n\n{page_text}",
            max_tokens=1500,
            response_json=True,
        )
    except Exception:
        return []

    if cost_tracker is not None:
        cost_tracker.record_anthropic(usage, scope=_SCOPE)
        await session.publish_event(_cost_update_event(cost_tracker.total_usd))

    parsed = _parse_json_response(text)
    if parsed is None:
        return []
    raw_findings = parsed.get("findings") or []
    if not isinstance(raw_findings, list):
        return []

    cleaned: list[dict] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        try:
            confidence = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        cleaned.append(
            {
                "type": str(item.get("type", "") or "unknown").strip(),
                "value": str(item.get("value", "") or "").strip(),
                "context": str(item.get("context", "") or "").strip(),
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
    return cleaned


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _risk_level_for_pii(pii_type: str) -> str:
    p = (pii_type or "").lower()
    if any(
        k in p
        for k in (
            "ssn",
            "social security",
            "credit card",
            "bank",
            "financial",
            "passport",
            "driver's license",
            "drivers license",
        )
    ):
        return "CRITICAL"
    if any(
        k in p
        for k in (
            "email",
            "phone",
            "mobile",
            "address",
            "home address",
            "geolocation",
            "coordinates",
            "ip address",
            "date of birth",
            "dob",
        )
    ):
        return "HIGH"
    if any(
        k in p
        for k in (
            "workplace",
            "employer",
            "company",
            "job",
            "school",
            "university",
            "college",
            "campus",
            "alma mater",
            "real name",
            "full name",
        )
    ):
        return "MEDIUM"
    return "LOW"


def _remediation_for(url: str, finding_type: str) -> str:
    domain = urlparse(url).netloc.lower() if url else ""
    if "reddit.com" in domain:
        return (
            f"Edit or delete your Reddit content at {url}. For posts you no "
            "longer control, request removal via reddit.com's data deletion "
            "form (reddit.com/settings/data-request)."
        )
    if "github.com" in domain:
        return (
            f"Remove the {finding_type} reference from {url}. Scrub commit "
            "history if the data appears in code, and rotate any exposed "
            "secrets immediately."
        )
    if "pastebin.com" in domain:
        return (
            f"Sign in to pastebin.com and delete the paste at {url}. If you "
            "do not own it, submit an abuse report at pastebin.com/abuse."
        )
    if domain.endswith(".edu"):
        return (
            f"Contact your institution's IT/registrar to request removal of "
            f"the personal data exposed at {url}."
        )
    if any(s in domain for s in ("facebook.com", "twitter.com", "x.com", "linkedin.com")):
        return (
            f"Sign in to {domain} and edit or delete the content at {url}; "
            "tighten profile privacy settings while you are there."
        )
    if not url:
        return (
            f"Contact the source site to request removal of personal {finding_type} data."
        )
    return (
        f"Request removal from {url} — contact the site's webmaster or "
        f"submit a takedown via {domain or 'the host'}'s privacy/abuse "
        "channel. If indexed by Google, also use the 'Remove outdated "
        "content' tool at search.google.com/search-console/remove-outdated-content."
    )


# ---------------------------------------------------------------------------
# Account probe + breach check (post-dork passes).
# ---------------------------------------------------------------------------


def _account_probe_remediation(site: str, url: str, category: str) -> str:
    if category == "professional":
        return (
            f"Lock down or remove the {site} profile at {url}. If the account "
            "isn't yours, report it for impersonation."
        )
    if category == "development":
        return (
            f"Audit your {site} profile at {url} for repos, gists, or commit "
            "history that expose personal data, and tighten visibility on what "
            "you can't delete."
        )
    return (
        f"Review the {site} account at {url} — set it private or delete it if "
        "it's still active."
    )


async def _run_account_probe(session: SessionState, username: str, http) -> None:
    settings = get_settings()
    if not settings.account_probe_enabled or not username:
        return
    try:
        hits = await _account_probe.probe(
            username,
            http,
            concurrency=settings.account_probe_max_concurrent,
        )
    except Exception:
        return

    findings_log: list[dict] = session.data.setdefault("findings", [])
    for hit in hits:
        finding = Finding(
            source="account_probe",
            evidence_chain=[
                f"Username probe: '{username}' resolves to a live page",
                f"Site: {hit.site} ({hit.category})",
                f"URL: {hit.url}",
            ],
            confidence=0.65,
            risk_level=hit.risk_level,  # type: ignore[arg-type]
            remediation=_account_probe_remediation(hit.site, hit.url, hit.category),
            metadata={
                "site": hit.site,
                "url": hit.url,
                "category": hit.category,
            },
        )
        payload = finding.to_dict()
        tagged = dict(payload)
        tagged["_pipeline"] = _PIPELINE
        findings_log.append(tagged)
        await session.publish_event(_finding_event(_PIPELINE, payload))


async def _run_breach_check(
    session: SessionState, http, seen_emails: set[str]
) -> None:
    settings = get_settings()
    if not seen_emails:
        return
    if not settings.hibp_api_key:
        await session.publish_event(
            _pipeline_status_event(
                _PIPELINE,
                "running",
                "HIBP_API_KEY not configured; breach lookup skipped.",
            )
        )
        return

    findings_log: list[dict] = session.data.setdefault("findings", [])
    for email in sorted(seen_emails):
        try:
            breaches = await _breach_check.check_email(
                email, settings.hibp_api_key, http
            )
        except Exception:
            continue
        for breach in breaches:
            risk = "CRITICAL" if breach.critical else "HIGH"
            data_class_str = ", ".join(breach.data_classes) or "unknown"
            finding = Finding(
                source="hibp",
                evidence_chain=[
                    f"Email: {email}",
                    f"Breach: {breach.title or breach.name}",
                    f"Date: {breach.breach_date}",
                    f"Data classes: {data_class_str}",
                ],
                confidence=0.9,
                risk_level=risk,  # type: ignore[arg-type]
                remediation=(
                    f"Change the password used at {breach.domain or breach.name} "
                    "and any service that reuses it. Enable 2FA. Monitor at "
                    "haveibeenpwned.com."
                ),
                metadata={
                    "email": email,
                    "breach_name": breach.name,
                    "breach_title": breach.title,
                    "breach_date": breach.breach_date,
                    "domain": breach.domain,
                    "data_classes": list(breach.data_classes),
                    "is_sensitive": breach.is_sensitive,
                    "is_verified": breach.is_verified,
                },
            )
            payload = finding.to_dict()
            tagged = dict(payload)
            tagged["_pipeline"] = _PIPELINE
            findings_log.append(tagged)
            await session.publish_event(_finding_event(_PIPELINE, payload))


async def _run_intelbase_lookup(
    session: SessionState, http, seen_emails: set[str]
) -> None:
    settings = get_settings()
    if not seen_emails:
        return
    if not settings.intelbase_api_key:
        await session.publish_event(
            _pipeline_status_event(
                _PIPELINE,
                "running",
                "INTELBASE_API_KEY not configured; IntelBase lookup skipped.",
            )
        )
        return

    findings_log: list[dict] = session.data.setdefault("findings", [])

    async def _emit(finding: Finding) -> None:
        payload = finding.to_dict()
        tagged = dict(payload)
        tagged["_pipeline"] = _PIPELINE
        findings_log.append(tagged)
        await session.publish_event(_finding_event(_PIPELINE, payload))

    for email in sorted(seen_emails):
        try:
            result = await _intelbase.lookup_email(
                email, settings.intelbase_api_key, http
            )
        except Exception:
            continue
        if result.is_empty:
            continue

        for breach in result.breaches:
            risk = "CRITICAL" if breach.critical else "HIGH"
            data_class_str = ", ".join(breach.data_classes) or "unknown"
            evidence_chain = [
                f"Email: {email}",
                f"Breach: {breach.title or breach.name or 'unknown source'}",
            ]
            if breach.breach_date:
                evidence_chain.append(f"Date: {breach.breach_date}")
            if data_class_str != "unknown":
                evidence_chain.append(f"Data classes: {data_class_str}")
            if breach.domain:
                evidence_chain.append(f"Domain: {breach.domain}")
            evidence_chain.append("Source: IntelBase /lookup/email")

            await _emit(
                Finding(
                    source="intelbase_breach",
                    evidence_chain=evidence_chain,
                    confidence=0.85,
                    risk_level=risk,  # type: ignore[arg-type]
                    remediation=(
                        f"Rotate the password used at "
                        f"{breach.domain or breach.name or 'this service'} and any "
                        "service that reuses it. Enable 2FA. Check "
                        "intelbase.is for the full breach record."
                    ),
                    metadata={
                        "email": email,
                        "breach_name": breach.name,
                        "breach_title": breach.title,
                        "breach_date": breach.breach_date,
                        "domain": breach.domain,
                        "data_classes": list(breach.data_classes),
                        "description": breach.description,
                    },
                )
            )

        # Linked-account hits are useful identity signals but lower-risk than
        # breaches. Bundle them into one finding per email so we don't drown
        # the dashboard if IntelBase returns dozens.
        if result.accounts:
            sites = sorted(
                {
                    a.site or _domain_of(a.url)
                    for a in result.accounts
                    if a.site or a.url
                }
            )
            evidence_chain = [
                f"Email: {email}",
                f"Linked accounts found on {len(sites)} site(s)",
            ]
            evidence_chain.extend(f"Account: {site}" for site in sites[:8])
            if len(sites) > 8:
                evidence_chain.append(f"... and {len(sites) - 8} more")
            evidence_chain.append("Source: IntelBase /lookup/email")

            await _emit(
                Finding(
                    source="intelbase_accounts",
                    evidence_chain=evidence_chain,
                    confidence=0.7,
                    risk_level="MEDIUM",
                    remediation=(
                        f"IntelBase identified accounts registered to {email}. "
                        "Audit each linked service, delete the ones you no "
                        "longer use, and consider an alias email going forward."
                    ),
                    metadata={
                        "email": email,
                        "site_count": len(sites),
                        "sites": sites,
                        "accounts": [
                            {
                                "site": a.site,
                                "url": a.url,
                                "username": a.username,
                            }
                            for a in result.accounts
                        ],
                    },
                )
            )


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


async def run(session: SessionState) -> None:
    await session.publish_event(_pipeline_status_event(_PIPELINE, "running"))

    try:
        settings = get_settings()
        profile: dict = session.data.get("profile") or {}
        username = str(profile.get("username", "") or "")
        full_name = str(profile.get("full_name", "") or "")

        if not username and not full_name:
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE, "error", "Profile missing username and full_name."
                )
            )
            return

        if not settings.serper_api_key:
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE,
                    "error",
                    "Serper credentials not configured (SERPER_API_KEY).",
                )
            )
            return

        http = session.data.get("http")
        if http is None:
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE, "error", "Shared httpx client not present in session."
                )
            )
            return

        cost_tracker = session.data.get("cost_tracker")

        # 1. Build query list — base dorks + Sonnet-generated custom dorks.
        # De-duplicate while preserving order: DorkER patterns overlap with
        # the hand-written base queries (e.g. site:github.com "{username}").
        max_total = max(1, int(settings.web_footprint_max_queries))
        seen: set[str] = set()
        deduped_base: list[str] = []
        for q in _base_queries(username, full_name):
            key = q.strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped_base.append(q)
            if len(deduped_base) >= max_total:
                break
        max_custom = max(0, max_total - len(deduped_base))
        custom_queries = await _generate_custom_queries(session, profile, max_custom)
        all_queries: list[str] = list(deduped_base)
        for q in custom_queries:
            key = q.strip().lower()
            if key and key not in seen:
                seen.add(key)
                all_queries.append(q)
            if len(all_queries) >= max_total:
                break

        if not all_queries:
            await session.publish_event(
                _pipeline_status_event(
                    _PIPELINE, "complete", "No queries generated."
                )
            )
            return

        cse = SerperClient(
            api_key=settings.serper_api_key,
            http=http,
        )
        fetcher = TrafilaturaFetcher(http=http)

        full_fetches_remaining = max(0, int(settings.web_footprint_max_full_fetches))
        seen_urls: set[str] = set()
        seen_emails: set[str] = set()

        # 2-7. Run queries → triage → extract → emit.
        for idx, query in enumerate(all_queries):
            if cost_tracker is not None and not cost_tracker.can_spend(
                _HAIKU_TRIAGE_COST_GUESS, scope=_SCOPE
            ):
                await session.publish_event(
                    _pipeline_status_event(
                        _PIPELINE,
                        "budget_exceeded",
                        f"Stopped before query {idx + 1}/{len(all_queries)}.",
                    )
                )
                return

            try:
                results = await cse.search(query, num=10)
            except SerperError as exc:
                # Surface non-fatal Serper issues but keep going to next query.
                if exc.status_code in (429, 401, 403):
                    await session.publish_event(
                        _pipeline_status_event(
                            _PIPELINE,
                            "error",
                            f"Serper refused requests ({exc.status_code}); halting.",
                        )
                    )
                    return
                continue
            except Exception:
                continue

            for result in results:
                url = result.get("link") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                if cost_tracker is not None and not cost_tracker.can_spend(
                    _HAIKU_TRIAGE_COST_GUESS, scope=_SCOPE
                ):
                    await session.publish_event(
                        _pipeline_status_event(
                            _PIPELINE,
                            "budget_exceeded",
                            "Stopped during triage stage.",
                        )
                    )
                    return

                triage = await _triage_result(session, result)
                if triage is None:
                    continue
                if not bool(triage.get("relevant")):
                    continue

                if full_fetches_remaining <= 0:
                    continue
                if cost_tracker is not None and not cost_tracker.can_spend(
                    _HAIKU_EXTRACT_COST_GUESS, scope=_SCOPE
                ):
                    await session.publish_event(
                        _pipeline_status_event(
                            _PIPELINE,
                            "budget_exceeded",
                            "Stopped before deep extraction.",
                        )
                    )
                    return

                page_text = await fetcher.extract_text(url, max_chars=5000)
                full_fetches_remaining -= 1
                if not page_text:
                    continue

                extracted = await _extract_pii(session, page_text, profile)
                for item in extracted:
                    value = item.get("value", "")
                    if not value:
                        continue
                    finding_type = item.get("type", "unknown") or "unknown"
                    confidence = float(item.get("confidence", 0.0) or 0.0)
                    risk_level = _risk_level_for_pii(finding_type)
                    finding = Finding(
                        source=f"web_dork_{idx}",
                        evidence_chain=[
                            f"Query: {query}",
                            f"URL: {url}",
                            f"Found: {finding_type}: {value}",
                            f"Context: {item.get('context', '')}",
                        ],
                        confidence=max(0.0, min(1.0, confidence)),
                        risk_level=risk_level,  # type: ignore[arg-type]
                        remediation=_remediation_for(url, finding_type),
                        metadata={
                            "query": query,
                            "query_index": idx,
                            "url": url,
                            "pii_type": finding_type,
                            "pii_value": value,
                            "displayLink": result.get("displayLink", ""),
                            "triage_reason": triage.get("relevance_reason", ""),
                            "triage_pii_types": triage.get("pii_types", []),
                        },
                    )
                    payload = finding.to_dict()
                    findings_log: list[dict] = session.data.setdefault(
                        "findings", []
                    )
                    tagged = dict(payload)
                    tagged["_pipeline"] = _PIPELINE
                    findings_log.append(tagged)
                    await session.publish_event(_finding_event(_PIPELINE, payload))
                    if _is_email_value(finding_type) and value:
                        seen_emails.add(value.lower())

        # ---- Post-dork pass: account probe + breach check ----------------
        await _run_account_probe(session, username, http)
        await _run_breach_check(session, http, seen_emails)
        await _run_intelbase_lookup(session, http, seen_emails)

        await session.publish_event(_pipeline_status_event(_PIPELINE, "complete"))
    except Exception as exc:  # noqa: BLE001 — defensive top-level guard
        await session.publish_event(
            _pipeline_status_event(_PIPELINE, "error", f"{type(exc).__name__}: {exc}")
        )
