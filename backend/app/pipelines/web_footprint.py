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
from app.services import email_permutator as _email_permutator
from app.services import emailrep as _emailrep
from app.services import github_lookup as _github_lookup
from app.services import gravatar as _gravatar
from app.services import intelbase as _intelbase
from app.services import linkedin_snippet as _linkedin_snippet
from app.services import searchcode as _searchcode
from app.services import spiderfoot_catalog as _sf_catalog
from app.services import wayback as _wayback
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
    "target person, decide if the page is likely about that target AND "
    "contains personally identifiable information.\n\n"
    "For 'relevance_reason': write ONE short plain-English sentence "
    "(<=100 chars) describing why. Do NOT paste raw snippet text, HTML, "
    "URLs, or quotes from the result.\n"
    "For 'pii_types': use short lowercase category labels only "
    "(e.g. 'email', 'phone', 'workplace', 'school'). No prose.\n\n"
    "Respond with JSON only: "
    "{\"relevant\": bool, \"relevance_reason\": str, \"pii_types\": [str]}"
)


def _extract_system_prompt(username: str, full_name: str) -> str:
    return (
        "You are an OSINT extractor analyzing a web page about a target "
        f"person (username: {username}, name: {full_name}). Extract only "
        "personally identifiable information that clearly belongs to this "
        "target. Skip ambiguous, generic, or unrelated values.\n\n"
        "Field rules:\n"
        "- 'type': short lowercase label (e.g. 'email', 'phone', "
        "'workplace', 'school', 'real_name', 'home_address').\n"
        "- 'value': the discovered value itself, nothing else.\n"
        "- 'context': ONE concise plain-English sentence (<=120 chars) "
        "describing where on the page it appeared and why it ties to the "
        "target. Do NOT paste raw page text, HTML, markdown, or quotes.\n"
        "- 'confidence': float between 0 and 1.\n\n"
        "Respond with JSON only: {\"findings\": [{\"type\": str, "
        "\"value\": str, \"context\": str, \"confidence\": float}]}"
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
    # SpiderFoot-derived patterns: paste sites, code hosts, leak archives.
    queries.extend(_sf_catalog.dorks_for_username(username))
    queries.extend(_sf_catalog.dorks_for_full_name(full_name))
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
    if anthropic is None or not settings.gemini_api_key:
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
            model=settings.gemini_pro_model,
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
    if anthropic is None or not settings.gemini_api_key:
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
            model=settings.gemini_fast_model,
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
    parsed["relevance_reason"] = _clean_one_liner(
        parsed.get("relevance_reason"), max_len=140
    )
    raw_types = parsed.get("pii_types") or []
    if isinstance(raw_types, list):
        parsed["pii_types"] = [
            _clean_one_liner(t, max_len=40) for t in raw_types if t
        ][:8]
    else:
        parsed["pii_types"] = []
    return parsed


async def _extract_pii(
    session: SessionState,
    page_text: str,
    profile: dict,
) -> list[dict]:
    settings = get_settings()
    cost_tracker = session.data.get("cost_tracker")
    anthropic = session.data.get("anthropic")
    if anthropic is None or not settings.gemini_api_key:
        return []
    if cost_tracker is not None and not cost_tracker.can_spend(
        _HAIKU_EXTRACT_COST_GUESS, scope=_SCOPE
    ):
        return []

    username = str(profile.get("username", "") or "")
    full_name = str(profile.get("full_name", "") or "")

    try:
        text, usage = await anthropic.call_text(
            model=settings.gemini_fast_model,
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
                "type": str(item.get("type", "") or "unknown").strip()[:40],
                "value": str(item.get("value", "") or "").strip()[:200],
                "context": _clean_one_liner(item.get("context", ""), max_len=140),
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
    return cleaned


def _clean_one_liner(raw: object, max_len: int) -> str:
    """Collapse whitespace, strip code fences/quotes, and truncate.

    Defends against models that ignore the prompt and dump multi-line raw
    page excerpts into a field that's supposed to be one short sentence.
    """
    if raw is None:
        return ""
    s = str(raw).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = s.strip().strip("`").strip("\"'").strip()
    s = " ".join(s.split())
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


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
    # Per-mode confidence: stronger evidence (two-sided WMN match) reads as
    # high confidence; weakest reliable signal (Sherlock message-only) reads
    # as moderate so a row of probe hits doesn't all show the same number.
    _MODE_CONFIDENCE = {
        "two_sided": 0.92,
        "status_and_message": 0.78,
        "status_only": 0.62,
        "message_only": 0.48,
    }
    _MODE_LABEL = {
        "two_sided": "WMN two-sided match (positive + negative markers)",
        "status_and_message": "Sherlock status + error-message check",
        "status_only": "Sherlock status_code check only",
        "message_only": "Sherlock message check + username echoed in body",
    }
    for hit in hits:
        confidence = _MODE_CONFIDENCE.get(hit.detection_mode, 0.55)
        method_label = _MODE_LABEL.get(hit.detection_mode, hit.detection_mode)
        finding = Finding(
            source="account_probe",
            evidence_chain=[
                f"Username probe: '{username}' resolves to a live page",
                f"Site: {hit.site} ({hit.category})",
                f"URL: {hit.url}",
                f"Detection: {method_label}",
            ],
            confidence=confidence,
            risk_level=hit.risk_level,  # type: ignore[arg-type]
            remediation=_account_probe_remediation(hit.site, hit.url, hit.category),
            metadata={
                "site": hit.site,
                "url": hit.url,
                "category": hit.category,
                "detection_mode": hit.detection_mode,
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


async def _run_searchcode_pivot(
    session: SessionState,
    http,
    username: str,
    seen_emails: set[str],
) -> None:
    """SpiderFoot sfp_searchcode-style pivot: probe searchcode.com for
    username + email mentions in public source repositories."""
    queries: list[str] = []
    if username:
        queries.append(username)
    queries.extend(sorted(seen_emails))
    if not queries:
        return

    findings_log: list[dict] = session.data.setdefault("findings", [])
    seen_repos: set[str] = set()

    for query in queries:
        try:
            hits = await _searchcode.search(query, http, max_results=12)
        except Exception:
            continue
        for hit in hits:
            repo_key = hit.repo or hit.file_url
            if not repo_key or repo_key in seen_repos:
                continue
            seen_repos.add(repo_key)

            evidence_chain = [
                f"searchcode query: {query!r}",
                f"Repo: {hit.repo or 'unknown'}",
            ]
            if hit.file_url:
                evidence_chain.append(f"File: {hit.file_url}")
            if hit.language:
                evidence_chain.append(f"Language: {hit.language}")
            if hit.snippet:
                evidence_chain.append(f"Snippet: {hit.snippet[:200]}")

            finding = Finding(
                source="searchcode",
                evidence_chain=evidence_chain,
                confidence=0.6,
                risk_level="MEDIUM",
                remediation=(
                    f"A reference to {query!r} appears in public source code "
                    f"at {hit.file_url or hit.repo}. If the repository is yours, "
                    "scrub commit history and rotate any exposed credentials. "
                    "If it isn't yours, request takedown or report under the "
                    "host's DMCA process."
                ),
                metadata={
                    "query": query,
                    "repo": hit.repo,
                    "file_url": hit.file_url,
                    "language": hit.language,
                },
            )
            payload = finding.to_dict()
            tagged = dict(payload)
            tagged["_pipeline"] = _PIPELINE
            findings_log.append(tagged)
            await session.publish_event(_finding_event(_PIPELINE, payload))


async def _run_gravatar_pivot(
    session: SessionState,
    http,
    seen_emails: set[str],
) -> None:
    """SpiderFoot sfp_gravatar-style pivot: for each surfaced email,
    fetch the Gravatar profile (free, no auth) and emit findings for any
    linked accounts, alternate emails, phone numbers, or real names."""
    if not seen_emails:
        return

    findings_log: list[dict] = session.data.setdefault("findings", [])

    for email in sorted(seen_emails):
        try:
            profile = await _gravatar.lookup_email(email, http)
        except Exception:
            continue
        if profile is None or profile.is_empty:
            continue

        evidence_chain = [
            f"Email: {email}",
            f"Gravatar profile: {profile.raw_url}",
        ]
        if profile.full_name:
            evidence_chain.append(f"Real name: {profile.full_name}")
        if profile.preferred_username:
            evidence_chain.append(
                f"Preferred username: {profile.preferred_username}"
            )
        if profile.extra_emails:
            evidence_chain.append(
                f"Other emails: {', '.join(profile.extra_emails[:5])}"
            )
        if profile.phone_numbers:
            evidence_chain.append(
                f"Phone numbers: {', '.join(profile.phone_numbers[:5])}"
            )
        if profile.accounts:
            sites = sorted(
                {a.site or _domain_of(a.url) for a in profile.accounts if a.site or a.url}
            )
            evidence_chain.append(
                f"Linked accounts ({len(sites)}): {', '.join(sites[:8])}"
            )

        risk = "HIGH" if (profile.phone_numbers or profile.full_name) else "MEDIUM"

        finding = Finding(
            source="gravatar",
            evidence_chain=evidence_chain,
            confidence=0.85,
            risk_level=risk,  # type: ignore[arg-type]
            remediation=(
                f"Gravatar exposes the profile linked to {email} (real name, "
                "linked socials, possible phone numbers) on every site that "
                "uses Gravatar avatars. Sign in at gravatar.com, scrub the "
                "profile fields you don't want public, or delete the account "
                "entirely if you don't recognise it."
            ),
            metadata={
                "email": email,
                "gravatar_url": profile.raw_url,
                "full_name": profile.full_name,
                "preferred_username": profile.preferred_username,
                "extra_emails": list(profile.extra_emails),
                "phone_numbers": list(profile.phone_numbers),
                "accounts": [
                    {"site": a.site, "url": a.url, "username": a.username}
                    for a in profile.accounts
                ],
            },
        )
        payload = finding.to_dict()
        tagged = dict(payload)
        tagged["_pipeline"] = _PIPELINE
        findings_log.append(tagged)
        await session.publish_event(_finding_event(_PIPELINE, payload))


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------------------
# Free email-discovery pivots (GitHub, permutator, Wayback, EmailRep).
# ---------------------------------------------------------------------------


import re as _re

# Lenient email regex for scanning Wayback / harvested HTML. The
# downstream Gravatar / HIBP / EmailRep probes weed out anything that
# isn't real, so false positives here only cost a few extra HTTP probes.
_EMAIL_RE = _re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


def _harvest_emails_from_text(text: str) -> set[str]:
    if not text:
        return set()
    out: set[str] = set()
    for m in _EMAIL_RE.findall(text):
        s = m.strip().strip(".,;:)>]\"'").lower()
        if "@" in s and "." in s.split("@", 1)[1]:
            out.add(s)
    return out


async def _run_github_lookup(
    session: SessionState,
    http,
    username: str,
    seen_emails: set[str],
) -> None:
    """Free GitHub harvest: profile email + recent-commit author emails.

    Cross-references discovered emails into ``seen_emails`` so the later
    Gravatar / HIBP / IntelBase / searchcode / EmailRep pivots all
    probe them.
    """
    if not username:
        return

    try:
        profile = await _github_lookup.lookup(username, http)
    except Exception:
        return
    if profile is None or profile.is_empty:
        return

    findings_log: list[dict] = session.data.setdefault("findings", [])

    new_emails: list[str] = []
    if profile.email:
        new_emails.append(profile.email)
    new_emails.extend(profile.commit_emails)

    evidence_chain = [
        f"GitHub user: {profile.login}",
        f"Profile: {profile.profile_url}",
    ]
    if profile.name:
        evidence_chain.append(f"Real name: {profile.name}")
    if profile.email:
        evidence_chain.append(f"Public profile email: {profile.email}")
    if profile.commit_emails:
        evidence_chain.append(
            f"Commit author emails: {', '.join(profile.commit_emails[:5])}"
        )
    if profile.noreply_commit_emails:
        evidence_chain.append(
            f"Noreply commit aliases: {len(profile.noreply_commit_emails)} "
            "(privacy-protected)"
        )
    if profile.company:
        evidence_chain.append(f"Company: {profile.company}")
    if profile.location:
        evidence_chain.append(f"Location: {profile.location}")
    if profile.blog:
        evidence_chain.append(f"Blog/site: {profile.blog}")
    if profile.twitter_username:
        evidence_chain.append(f"Twitter: @{profile.twitter_username}")

    risk = "HIGH" if new_emails else "MEDIUM"
    finding = Finding(
        source="github_lookup",
        evidence_chain=evidence_chain,
        confidence=0.9 if new_emails else 0.7,
        risk_level=risk,  # type: ignore[arg-type]
        remediation=(
            "Edit your GitHub profile at github.com/settings/profile and "
            "either remove the public email, set it to a noreply alias "
            "(github.com/settings/emails — 'Keep my email addresses "
            "private'), or rewrite git history to scrub the leaked address. "
            "Run `git config --global user.email <noreply>` so future "
            "commits don't re-leak it."
        ),
        metadata={
            "github_login": profile.login,
            "profile_url": profile.profile_url,
            "name": profile.name,
            "profile_email": profile.email,
            "commit_emails": list(profile.commit_emails),
            "noreply_commit_emails": list(profile.noreply_commit_emails),
            "company": profile.company,
            "location": profile.location,
            "blog": profile.blog,
            "twitter_username": profile.twitter_username,
            "public_repos": profile.public_repos,
        },
    )
    payload = finding.to_dict()
    tagged = dict(payload)
    tagged["_pipeline"] = _PIPELINE
    findings_log.append(tagged)
    await session.publish_event(_finding_event(_PIPELINE, payload))

    for e in new_emails:
        e_low = e.strip().lower()
        if e_low and "@" in e_low:
            seen_emails.add(e_low)


async def _run_email_permutator(
    session: SessionState,
    http,
    profile: dict,
    username: str,
    seen_emails: set[str],
    extra_domain_hints: tuple[str, ...] = (),
) -> None:
    """Generate likely emails from full_name × common providers, then
    confirm each by probing Gravatar (free, fast). Confirmed emails get
    added to ``seen_emails`` so downstream pivots cross-reference them.

    ``extra_domain_hints`` are domains the caller has independently
    surfaced (e.g. an employer name from a LinkedIn snippet → guessed
    ``employer.com``). They go to the front of the candidate list
    because work emails on a target's actual employer domain are the
    highest-value find.
    """
    full_name = str(profile.get("full_name", "") or "")
    if not full_name:
        return

    extra_list: list[str] = []
    for h in extra_domain_hints:
        h_clean = (h or "").strip().lower()
        if h_clean and "." in h_clean and h_clean not in extra_list:
            extra_list.append(h_clean)

    ext_url = str(profile.get("external_url", "") or "")
    domain = _email_permutator.domain_from_url(ext_url)
    if domain and "." in domain and domain not in (
        "instagram.com",
        "linktr.ee",
        "linktree.com",
        "beacons.ai",
        "bio.link",
    ) and domain not in extra_list:
        extra_list.append(domain)

    extra: tuple[str, ...] = tuple(extra_list)

    candidates = _email_permutator.generate_candidates(
        full_name=full_name,
        username=username,
        extra_domains=extra,
        max_candidates=40,
    )
    if not candidates:
        return

    findings_log: list[dict] = session.data.setdefault("findings", [])

    confirmed: list[str] = []
    for candidate in candidates:
        if candidate in seen_emails:
            continue
        try:
            gprofile = await _gravatar.lookup_email(candidate, http)
        except Exception:
            continue
        if gprofile is None:
            continue
        # A 200 response with parseable JSON confirms the address is
        # registered to a real Gravatar account, even if the entry is
        # sparse.
        confirmed.append(candidate)
        seen_emails.add(candidate)

        evidence_chain = [
            f"Permutator candidate: {candidate}",
            f"Generated from: full_name='{full_name}'"
            + (f", username='{username}'" if username else ""),
            f"Confirmed via Gravatar: {gprofile.raw_url}",
        ]
        if gprofile.full_name:
            evidence_chain.append(f"Gravatar real name: {gprofile.full_name}")
        if gprofile.preferred_username:
            evidence_chain.append(
                f"Gravatar username: {gprofile.preferred_username}"
            )

        finding = Finding(
            source="email_permutator",
            evidence_chain=evidence_chain,
            confidence=0.8,
            risk_level="HIGH",
            remediation=(
                f"The address {candidate} is one of the most-likely "
                "permutations of your name and was confirmed live via "
                "Gravatar. Audit and lock down the Gravatar profile at "
                "gravatar.com, and consider migrating to a less-guessable "
                "email alias for future signups."
            ),
            metadata={
                "candidate": candidate,
                "full_name": full_name,
                "username": username,
                "gravatar_url": gprofile.raw_url,
                "gravatar_full_name": gprofile.full_name,
                "gravatar_preferred_username": gprofile.preferred_username,
            },
        )
        payload = finding.to_dict()
        tagged = dict(payload)
        tagged["_pipeline"] = _PIPELINE
        findings_log.append(tagged)
        await session.publish_event(_finding_event(_PIPELINE, payload))

    if confirmed:
        await session.publish_event(
            _pipeline_status_event(
                _PIPELINE,
                "running",
                f"Email permutator confirmed {len(confirmed)} candidate(s) via Gravatar.",
            )
        )


# Hosts whose live HTML is gated when logged-out — Wayback snapshots
# are usually the only way to scrape their public profile body.
_WAYBACK_TARGET_HOSTS = (
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "quora.com",
    "medium.com",
)


async def _run_wayback_pivot(
    session: SessionState,
    http,
    seen_urls: set[str],
    seen_emails: set[str],
) -> None:
    """Fetch Wayback snapshots for any gated-domain URLs surfaced during
    dorking and harvest emails from the archived HTML."""
    if not seen_urls:
        return

    targets = [
        u for u in seen_urls
        if any(h in (urlparse(u).netloc or "").lower() for h in _WAYBACK_TARGET_HOSTS)
    ]
    if not targets:
        return

    # Cap aggressively — Wayback snapshots are heavy and IA gets cranky
    # under sustained load. Top 5 LinkedIn-class URLs is plenty.
    targets = targets[:5]

    findings_log: list[dict] = session.data.setdefault("findings", [])

    for url in targets:
        try:
            snapshot = await _wayback.latest_snapshot(url, http)
        except Exception:
            continue
        if snapshot is None:
            continue
        try:
            text = await _wayback.fetch_snapshot_text(snapshot, http)
        except Exception:
            text = ""

        harvested = _harvest_emails_from_text(text)
        new_for_url = sorted(harvested - seen_emails)

        evidence_chain = [
            f"Original URL (gated/altered): {snapshot.original_url}",
            f"Wayback snapshot: {snapshot.snapshot_url}",
            f"Snapshot date: {snapshot.timestamp[:8]}",
        ]
        if new_for_url:
            evidence_chain.append(
                f"Emails harvested from snapshot: {', '.join(new_for_url[:5])}"
            )
        else:
            evidence_chain.append(
                "No new emails in snapshot, but archived body is publicly readable."
            )

        risk = "HIGH" if new_for_url else "MEDIUM"
        finding = Finding(
            source="wayback",
            evidence_chain=evidence_chain,
            confidence=0.75 if new_for_url else 0.55,
            risk_level=risk,  # type: ignore[arg-type]
            remediation=(
                f"The Internet Archive holds a snapshot of {snapshot.original_url} "
                f"taken on {snapshot.timestamp[:8]}. Even if you've since edited "
                "or deleted the live page, the archived copy is public. Submit "
                "a removal request via archive.org/about/contact (cite the "
                f"snapshot URL: {snapshot.snapshot_url})."
            ),
            metadata={
                "original_url": snapshot.original_url,
                "snapshot_url": snapshot.snapshot_url,
                "timestamp": snapshot.timestamp,
                "harvested_emails": new_for_url,
            },
        )
        payload = finding.to_dict()
        tagged = dict(payload)
        tagged["_pipeline"] = _PIPELINE
        findings_log.append(tagged)
        await session.publish_event(_finding_event(_PIPELINE, payload))

        for e in new_for_url:
            seen_emails.add(e)


async def _run_linkedin_snippet_harvest(
    session: SessionState,
    cse: SerperClient,
    full_name: str,
    username: str,
    city_hint: str,
) -> list:
    """Run LinkedIn-targeted dorks and parse the search snippets into
    structured fields. We never fetch LinkedIn HTML directly — Google's
    snippet is the only logged-out view of the public profile body.

    Each parsed profile becomes a finding. Returns the parsed snippets
    so the caller can fold employer/school/location hints back into
    other pivots (e.g. employer domain → email permutator).
    """
    if not full_name and not username:
        return []

    try:
        snippets = await _linkedin_snippet.harvest(
            cse,
            full_name=full_name,
            username=username,
            city_hint=city_hint,
            max_queries=4,
            max_per_query=8,
        )
    except Exception:
        return []
    if not snippets:
        return []

    findings_log: list[dict] = session.data.setdefault("findings", [])

    for snip in snippets:
        evidence_chain = [
            f"LinkedIn profile: {snip.profile_url}",
            "Source: Google search snippet (LinkedIn HTML is gated logged-out)",
        ]
        if snip.name:
            evidence_chain.append(f"Name: {snip.name}")
        if snip.headline_company:
            evidence_chain.append(f"Headline company: {snip.headline_company}")
        if snip.employer:
            evidence_chain.append(f"Experience: {snip.employer}")
        if snip.school:
            evidence_chain.append(f"Education: {snip.school}")
        loc_parts = [p for p in (snip.city, snip.region, snip.country) if p]
        if loc_parts:
            evidence_chain.append(f"Location: {', '.join(loc_parts)}")
        if snip.postal_code:
            evidence_chain.append(f"Postal code: {snip.postal_code}")
        if snip.headline:
            evidence_chain.append(f"Headline: {snip.headline[:200]}")
        if snip.followers:
            evidence_chain.append(f"Followers: {snip.followers}")
        if snip.connections:
            evidence_chain.append(f"Connections: {snip.connections}")

        # Postal-code or precise city/employer pairing is HIGH because it
        # narrows the target's home/work neighborhood. School-only is
        # MEDIUM.
        risk = "HIGH" if (snip.postal_code or (snip.city and snip.employer)) else "MEDIUM"

        finding = Finding(
            source="linkedin_snippet",
            evidence_chain=evidence_chain,
            confidence=0.85,
            risk_level=risk,  # type: ignore[arg-type]
            remediation=(
                f"Tighten LinkedIn profile visibility at {snip.profile_url}. "
                "Settings → Visibility → 'Edit your public profile' lets you "
                "hide each field (location, employer, education, "
                "headline) from logged-out viewers and search engines. "
                "Fields you don't hide are scraped into Google snippets "
                "and end up in dossiers like this one."
            ),
            metadata={
                "profile_url": snip.profile_url,
                "name": snip.name,
                "employer": snip.employer,
                "headline_company": snip.headline_company,
                "school": snip.school,
                "city": snip.city,
                "region": snip.region,
                "country": snip.country,
                "postal_code": snip.postal_code,
                "headline": snip.headline,
                "followers": snip.followers,
                "connections": snip.connections,
                "raw_title": snip.title,
                "raw_snippet": snip.snippet,
            },
        )
        payload = finding.to_dict()
        tagged = dict(payload)
        tagged["_pipeline"] = _PIPELINE
        findings_log.append(tagged)
        await session.publish_event(_finding_event(_PIPELINE, payload))

    return snippets


async def _run_emailrep_pivot(
    session: SessionState,
    http,
    seen_emails: set[str],
) -> None:
    """EmailRep.io free reputation/existence cross-reference. Caps at
    8 lookups per audit because the anonymous tier is severely
    rate-limited."""
    if not seen_emails:
        return

    targets = sorted(seen_emails)[:8]

    findings_log: list[dict] = session.data.setdefault("findings", [])

    rate_limited = False
    for email in targets:
        if rate_limited:
            break
        try:
            result = await _emailrep.lookup_email(email, http)
        except Exception:
            continue
        if result is None:
            # Could be 429 — assume rate-limit and stop further probes.
            rate_limited = True
            continue
        if not result.has_signal:
            continue

        evidence_chain = [
            f"Email: {email}",
            f"EmailRep: {result.raw_url}",
        ]
        if result.reputation:
            evidence_chain.append(f"Reputation: {result.reputation}")
        if result.deliverable:
            evidence_chain.append("Deliverable: yes (MX-confirmed)")
        if result.first_seen:
            evidence_chain.append(f"First seen: {result.first_seen}")
        if result.profiles:
            evidence_chain.append(
                f"Registered on: {', '.join(result.profiles[:8])}"
            )
        if result.data_breach:
            evidence_chain.append("Found in known data breaches.")
        if result.credentials_leaked:
            evidence_chain.append("Credentials previously leaked.")

        risk = "CRITICAL" if result.credentials_leaked else (
            "HIGH" if (result.data_breach or result.profiles) else "MEDIUM"
        )

        finding = Finding(
            source="emailrep",
            evidence_chain=evidence_chain,
            confidence=0.8,
            risk_level=risk,  # type: ignore[arg-type]
            remediation=(
                f"EmailRep.io aggregates public reputation signals for "
                f"{email}. The address is a known identifier across "
                f"{len(result.profiles)} site(s); rotate any reused "
                "passwords, enable 2FA on every linked account, and "
                "consider an email alias for new signups."
            ),
            metadata={
                "email": email,
                "reputation": result.reputation,
                "suspicious": result.suspicious,
                "deliverable": result.deliverable,
                "data_breach": result.data_breach,
                "credentials_leaked": result.credentials_leaked,
                "first_seen": result.first_seen,
                "last_seen": result.last_seen,
                "profiles": list(result.profiles),
            },
        )
        payload = finding.to_dict()
        tagged = dict(payload)
        tagged["_pipeline"] = _PIPELINE
        findings_log.append(tagged)
        await session.publish_event(_finding_event(_PIPELINE, payload))


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
                    source_host = (
                        result.get("displayLink", "") or _domain_of(url)
                    )
                    pretty_type = finding_type.replace("_", " ")
                    chain: list[str] = []
                    if source_host:
                        chain.append(f"Source: {source_host}")
                    chain.append(f"URL: {url}")
                    chain.append(f"Found: {pretty_type} — {value}")
                    ctx = (item.get("context") or "").strip()
                    if ctx:
                        chain.append(f"Why: {ctx}")
                    finding = Finding(
                        source=f"web_dork_{idx}",
                        evidence_chain=chain,
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

        # ---- Post-dork pass: surface more emails before the cross-ref ----
        # Order matters: each step here can add to ``seen_emails``, and
        # later pivots cross-reference everything in that set.
        await _run_account_probe(session, username, http)

        # 1. GitHub harvest (free) — public profile email + commit
        #    author emails. Biggest single source of personal email
        #    leakage for any developer target.
        await _run_github_lookup(session, http, username, seen_emails)

        # 2. LinkedIn snippet harvest — Google snippets for LinkedIn
        #    profiles expose employer, education, city, postal code,
        #    follower count *without* fetching the gated LinkedIn HTML.
        #    Run before the permutator so the discovered employer
        #    domain can seed extra email candidates.
        li_snippets = await _run_linkedin_snippet_harvest(
            session, cse, full_name, username, city_hint=""
        )
        # Build extra email-domain hints from any discovered employers.
        permutator_extra_domains: list[str] = []
        for snip in li_snippets:
            for emp in (snip.employer, snip.headline_company):
                if not emp:
                    continue
                # "Geotab" -> "geotab.com" guess. Crude but correct
                # often enough that the Gravatar probe is worth a try.
                slug = _re.sub(r"[^a-z0-9]+", "", emp.lower())
                if slug and len(slug) >= 3:
                    permutator_extra_domains.append(f"{slug}.com")

        # 3. Email permutator (free) — generate name-based candidates and
        #    confirm them via Gravatar. Confirmed candidates flow into
        #    seen_emails for the rest of the cross-reference loop.
        await _run_email_permutator(
            session,
            http,
            profile,
            username,
            seen_emails,
            extra_domain_hints=tuple(permutator_extra_domains),
        )

        # 3. Wayback Machine (free) — pull archived snapshots of any
        #    LinkedIn / FB / X / Quora / Medium URLs surfaced during
        #    dorking; harvest emails from the archived HTML.
        await _run_wayback_pivot(session, http, seen_urls, seen_emails)

        # ---- Cross-reference pass: feed every email through every lookup -
        await _run_breach_check(session, http, seen_emails)
        await _run_intelbase_lookup(session, http, seen_emails)
        # SpiderFoot-derived + free pivots.
        await _run_searchcode_pivot(session, http, username, seen_emails)
        await _run_gravatar_pivot(session, http, seen_emails)
        # 4. EmailRep.io (free) — final reputation / known-profile
        #    cross-reference per discovered email.
        await _run_emailrep_pivot(session, http, seen_emails)

        await session.publish_event(_pipeline_status_event(_PIPELINE, "complete"))
    except Exception as exc:  # noqa: BLE001 — defensive top-level guard
        await session.publish_event(
            _pipeline_status_event(_PIPELINE, "error", f"{type(exc).__name__}: {exc}")
        )
