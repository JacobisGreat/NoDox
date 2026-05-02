"""LinkedIn snippet harvester.

LinkedIn HTML is gated for logged-out clients, but Google indexes the
public profile text and reproduces it in the search-result snippet.
That snippet is often the only free, programmatic view of a target's
LinkedIn profile and contains gold:

    "Yohance Pawania - Geotab"
    "LinkedIn · Yohance Pawania"
    "620+ followers"
    "Oakville, Ontario, Canada · Geotab"
    "passionate about compilers, physics and game theory · Experience:
     Geotab · Education: Abbey Park High School · Location: L6M 0A1
     · 500+ connections on ..."

This module:

1. Runs LinkedIn-targeted Serper queries (no LinkedIn fetch — we never
   touch their gated HTML).
2. Parses the search snippets into structured fields (employer,
   education, city, postal code, follower / connection counts, headline).
3. Returns a list of ``LinkedInSnippet`` records ready for the
   pipeline to emit as findings and to cross-reference against other
   surface area (employer name → email permutator domain hint, etc.).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.services.serper import SerperClient, SerperError

logger = logging.getLogger(__name__)


# Google reproduces the LinkedIn body using `·` as a delimiter between
# stanzas. These regexes pull each stanza out — they tolerate trailing
# `· ` separators and the snippet's frequent ellipsis/Unicode marks.
_RE_LOCATION_LABEL = re.compile(
    r"Location:\s*([^·]+?)(?=\s*·|$)", re.I
)
_RE_EXPERIENCE = re.compile(
    r"Experience:\s*([^·]+?)(?=\s*·|$)", re.I
)
_RE_EDUCATION = re.compile(
    r"Education:\s*([^·]+?)(?=\s*·|$)", re.I
)
_RE_FOLLOWERS = re.compile(
    r"([\d,]+\+?)\s*followers", re.I
)
_RE_CONNECTIONS = re.compile(
    r"([\d,]+\+?)\s*connections", re.I
)
# Heuristic for "City, Region, Country" (3-clause comma-separated phrase
# that appears near the start of LinkedIn snippets). 2-clause variants
# are common too ("Oakville, Canada"), so we accept either.
_RE_CITY_LINE = re.compile(
    r"([A-Z][\w\-\.']*(?:\s+[A-Z][\w\-\.']*)*)"
    r",\s*([A-Z][\w\-\.']*(?:\s+[A-Z][\w\-\.']*)*)"
    r"(?:,\s*([A-Z][\w\-\.']*(?:\s+[A-Z][\w\-\.']*)*))?"
)
# Postal codes — Canadian (A1A 1A1), US ZIP (5 or 9-digit), UK
# (SW1A 1AA-style). LinkedIn's "Location:" field often holds these.
_RE_POSTAL = re.compile(
    r"\b("
    r"[A-Z]\d[A-Z]\s*\d[A-Z]\d"      # CA: K1A 0B1
    r"|\d{5}(?:-\d{4})?"              # US: 90210 / 90210-1234
    r"|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}"  # UK: SW1A 1AA
    r")\b"
)
# LinkedIn profile URL filter — distinguishes /in/<slug> from /pub or
# /posts which carry less structured content.
_RE_LI_PROFILE = re.compile(
    r"linkedin\.com/(in|pub)/[\w\-%]+", re.I
)


@dataclass(frozen=True)
class LinkedInSnippet:
    profile_url: str
    title: str                  # raw search-result title
    snippet: str                # raw search-result snippet text
    name: str = ""              # extracted from title or "LinkedIn · Name"
    headline_company: str = ""  # company from title "Name - Company" pattern
    employer: str = ""          # from "Experience:" stanza
    school: str = ""            # from "Education:" stanza
    city: str = ""              # from city line
    region: str = ""            # state/province
    country: str = ""
    postal_code: str = ""       # extracted via regex from Location line
    headline: str = ""          # bio/headline before "Experience:" stanza
    followers: str = ""         # "620+" / "500"
    connections: str = ""

    @property
    def has_signal(self) -> bool:
        return bool(
            self.employer
            or self.school
            or self.city
            or self.postal_code
            or self.headline_company
            or self.followers
            or self.connections
        )


def queries_for(full_name: str, username: str = "", city_hint: str = "") -> list[str]:
    """Targeted dorks that maximize LinkedIn snippet exposure.

    Order matters — the most-likely-to-hit-a-real-profile queries come
    first so a caller capping at N queries still gets the best results.
    """
    queries: list[str] = []

    if full_name:
        queries.append(f'site:linkedin.com/in "{full_name}"')
        queries.append(f'"{full_name}" linkedin')
        if city_hint:
            queries.append(f'site:linkedin.com/in "{full_name}" "{city_hint}"')

    if username:
        queries.append(f'site:linkedin.com/in "{username}"')
        queries.append(f'"{username}" linkedin')

    return queries


def parse_snippet(
    title: str, snippet: str, profile_url: str
) -> LinkedInSnippet | None:
    """Parse a Serper result into a ``LinkedInSnippet``. Returns None if
    the URL doesn't look like a LinkedIn profile or no signal could be
    extracted."""
    if not profile_url or not _RE_LI_PROFILE.search(profile_url or ""):
        return None

    text = " ".join(filter(None, (title or "", snippet or "")))

    # Title prefix (before " - ") is the cleanest name source — Google
    # uses "{Name} - {Company}" verbatim from LinkedIn's <title>. Only
    # fall back to "LinkedIn · {Name}" when the title is missing or
    # uses a different format.
    name = ""
    if " - " in (title or ""):
        name = (title or "").split(" - ", 1)[0].strip()
    if not name:
        m = re.search(
            r"LinkedIn\s*[·•]\s*(.+?)"
            r"(?=\s+[\d,]+\+?\s*followers|\s+[\d,]+\+?\s*connections|\s*[·•]|$)",
            text,
        )
        if m:
            name = m.group(1).strip()

    headline_company = ""
    if " - " in (title or ""):
        parts = (title or "").rsplit(" - ", 1)
        if len(parts) == 2:
            headline_company = parts[1].strip()

    experience = _first_group(_RE_EXPERIENCE, text)
    education = _first_group(_RE_EDUCATION, text)
    location_label = _first_group(_RE_LOCATION_LABEL, text)
    followers = _first_group(_RE_FOLLOWERS, text)
    connections = _first_group(_RE_CONNECTIONS, text)

    postal = ""
    if location_label:
        pm = _RE_POSTAL.search(location_label)
        if pm:
            postal = pm.group(1).strip()

    # City line: search the snippet body but skip text after "Experience:"
    # / "Education:" so we don't catch headline phrases by accident.
    body_for_city = re.split(
        r"\b(?:Experience|Education):", text, maxsplit=1, flags=re.I
    )[0]
    city = region = country = ""
    cm = _RE_CITY_LINE.search(body_for_city)
    if cm:
        city = (cm.group(1) or "").strip()
        region = (cm.group(2) or "").strip()
        country = (cm.group(3) or "").strip()
        # Filter out false-positives where the regex lands on something
        # like "Software Engineer, Geotab" — drop if the first token is a
        # known-non-place word.
        if city.lower() in _NON_PLACE_PREFIXES:
            city = region = country = ""

    # Headline = the snippet body before "Experience:" minus all the
    # structured fragments we've already extracted (title, name, city
    # line, follower / connection counts). Best-effort cleanup.
    headline = body_for_city
    if title:
        headline = headline.replace(title, " ")
    if name:
        headline = headline.replace(name, " ")
    if headline_company:
        headline = headline.replace(headline_company, " ")
    headline = _RE_FOLLOWERS.sub(" ", headline)
    headline = _RE_CONNECTIONS.sub(" ", headline)
    if cm:
        headline = headline.replace(cm.group(0), " ")
    headline = re.sub(
        r"LinkedIn\s*[·•]?\s*", " ", headline, flags=re.I
    )
    headline = re.sub(r"\s+", " ", headline).strip(" ·•-,")

    parsed = LinkedInSnippet(
        profile_url=profile_url,
        title=(title or "").strip(),
        snippet=(snippet or "").strip(),
        name=name,
        headline_company=headline_company,
        employer=experience,
        school=education,
        city=city,
        region=region,
        country=country,
        postal_code=postal,
        headline=headline,
        followers=followers,
        connections=connections,
    )
    return parsed if parsed.has_signal else None


_NON_PLACE_PREFIXES = {
    "experience",
    "education",
    "location",
    "linkedin",
    "skills",
    "summary",
}


def _first_group(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    value = (m.group(1) or "").strip()
    # Snippets often end with " ..." — strip that and any trailing
    # punctuation runs.
    value = re.sub(r"\s*\.{2,}\s*$", "", value)
    return value.strip(" ·•-,")


async def harvest(
    cse: SerperClient,
    full_name: str,
    username: str = "",
    city_hint: str = "",
    max_queries: int = 4,
    max_per_query: int = 8,
) -> list[LinkedInSnippet]:
    """End-to-end harvest: run LinkedIn-targeted dorks, dedupe by
    profile URL, return parsed snippets. Caller is responsible for
    cost-tracker accounting (Serper is paid; the LLM is not invoked
    here)."""
    queries = queries_for(full_name, username, city_hint)
    if not queries:
        return []
    queries = queries[:max(1, max_queries)]

    seen_urls: set[str] = set()
    results: list[LinkedInSnippet] = []

    for query in queries:
        try:
            hits = await cse.search(query, num=max_per_query)
        except SerperError as exc:
            if exc.status_code in (401, 403, 429):
                logger.debug("Serper refused LinkedIn query (%s); aborting.", exc.status_code)
                break
            continue
        except Exception as exc:
            logger.debug("Serper threw on LinkedIn query: %s", exc)
            continue

        for hit in hits:
            url = (hit.get("link") or "").strip()
            if not url or url in seen_urls:
                continue
            host = (urlparse(url).netloc or "").lower()
            if "linkedin.com" not in host:
                continue
            seen_urls.add(url)

            parsed = parse_snippet(
                title=hit.get("title") or "",
                snippet=hit.get("snippet") or "",
                profile_url=url,
            )
            if parsed is not None:
                results.append(parsed)

    return results
