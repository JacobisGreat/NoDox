"""GitHub public-API harvest.

Free, no-auth lookup of a target's GitHub profile + recent public events
to surface emails, real names, linked socials, and company. GitHub is
the single largest free email-leak surface for developers — many users
unknowingly attach their personal email to every public commit through
``git config user.email`` and the email becomes visible via the events
API even when their profile email is private.

Two endpoints, both unauthenticated:

- ``GET https://api.github.com/users/{login}`` — profile fields
- ``GET https://api.github.com/users/{login}/events/public`` — last
  ~30 public events; ``PushEvent`` payloads carry per-commit
  ``author.email``.

Anonymous rate limit is 60 req/h per source IP, so callers should treat
HTTP 403 with ``X-RateLimit-Remaining: 0`` as a soft skip rather than a
hard error.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


_PROFILE_URL = "https://api.github.com/users/{login}"
_EVENTS_URL = "https://api.github.com/users/{login}/events/public"
_COMMIT_URL = "https://api.github.com/repos/{repo}/commits/{sha}"
_USER_AGENT = "NODOXX-self-audit/0.1"
_TIMEOUT_SECONDS = 10.0
_MAX_EVENTS_PAGES = 1  # 30 events ~ enough for recent commit emails

# Anonymous GitHub limit is 60 req/h per IP. Profile + events + N
# commit fetches must fit in that budget when combined with whatever
# else the audit is hitting on the same IP.
_MAX_COMMIT_FETCHES = 6

# GitHub-managed noreply / bot / auto-generated addresses. These don't
# reveal the user's real inbox so we tag them separately instead of
# dropping them outright. Variants seen in the wild:
#   12345678+username@users.noreply.github.com   (digit-prefix noreply)
#   username@users.noreply.github.com            (legacy noreply)
#   noreply@github.com                           (generic noreply)
#   12345678+github-actions[bot]@users.noreply.github.com  (bot)
#   action@github.com                            (Actions runner)
_NOREPLY_RE = re.compile(
    r"(@users\.noreply\.github\.com$|^noreply@github\.com$|"
    r"^action@github\.com$|\[bot\]@)",
    re.I,
)


@dataclass(frozen=True)
class GitHubProfile:
    login: str
    name: str = ""
    email: str = ""           # public profile email (rare)
    bio: str = ""
    company: str = ""
    location: str = ""
    blog: str = ""            # personal site URL
    twitter_username: str = ""
    public_repos: int = 0
    profile_url: str = ""
    commit_emails: tuple[str, ...] = field(default_factory=tuple)
    noreply_commit_emails: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not (
            self.name
            or self.email
            or self.bio
            or self.company
            or self.location
            or self.blog
            or self.twitter_username
            or self.commit_emails
        )


async def lookup(
    username: str, http: httpx.AsyncClient
) -> GitHubProfile | None:
    """Fetch the GitHub profile and recent commit emails for ``username``.

    Returns ``None`` if the user does not exist or the rate limit is
    exhausted. Catches its own HTTP errors so callers can rely on the
    return value alone.
    """
    if not username:
        return None

    profile_payload = await _get_json(
        http, _PROFILE_URL.format(login=username)
    )
    if profile_payload is None:
        return None

    real_emails, noreply_emails = await _harvest_commit_emails(http, username)

    return GitHubProfile(
        login=str(profile_payload.get("login") or username),
        name=str(profile_payload.get("name") or "").strip(),
        email=str(profile_payload.get("email") or "").strip(),
        bio=str(profile_payload.get("bio") or "").strip(),
        company=str(profile_payload.get("company") or "").strip(),
        location=str(profile_payload.get("location") or "").strip(),
        blog=str(profile_payload.get("blog") or "").strip(),
        twitter_username=str(
            profile_payload.get("twitter_username") or ""
        ).strip(),
        public_repos=int(profile_payload.get("public_repos") or 0),
        profile_url=str(profile_payload.get("html_url") or ""),
        commit_emails=tuple(real_emails),
        noreply_commit_emails=tuple(noreply_emails),
    )


async def _harvest_commit_emails(
    http: httpx.AsyncClient, username: str
) -> tuple[list[str], list[str]]:
    """Walk the user's recent PushEvents and hit /repos/.../commits/{sha}
    for each one to extract the author email.

    GitHub's events API stopped returning inline ``commits[]`` arrays in
    push payloads; the head SHA is now the only handle. Each commit
    fetch costs one anonymous-tier API call, so we cap at
    ``_MAX_COMMIT_FETCHES``.
    """
    real: list[str] = []
    noreply: list[str] = []
    seen: set[str] = set()

    # 1. Pull recent events.
    pushes: list[tuple[str, str]] = []  # (repo_full_name, sha)
    for page in range(1, _MAX_EVENTS_PAGES + 1):
        url = _EVENTS_URL.format(login=username) + f"?per_page=100&page={page}"
        payload = await _get_json(http, url)
        if not isinstance(payload, list) or not payload:
            break

        for event in payload:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "PushEvent":
                continue
            repo = (event.get("repo") or {}).get("name") or ""
            head = (event.get("payload") or {}).get("head") or ""
            if repo and head:
                pushes.append((str(repo), str(head)))

    # De-dupe SHAs (a force-push to the same head shows multiple times).
    seen_shas: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for repo, sha in pushes:
        if sha in seen_shas:
            continue
        seen_shas.add(sha)
        deduped.append((repo, sha))

    # 2. For each push, fetch the head commit and extract author email.
    fetches = 0
    for repo, sha in deduped:
        if fetches >= _MAX_COMMIT_FETCHES:
            break
        if len(real) >= 3:
            # Three real (non-noreply) emails is plenty for a finding —
            # additional fetches are unlikely to surface a new one.
            break
        commit_url = _COMMIT_URL.format(repo=repo, sha=sha)
        commit_payload = await _get_json(http, commit_url)
        fetches += 1
        if not isinstance(commit_payload, dict):
            continue

        commit_node = commit_payload.get("commit") or {}
        for role in ("author", "committer"):
            person = commit_node.get(role) if isinstance(commit_node, dict) else None
            if not isinstance(person, dict):
                continue
            email = str(person.get("email") or "").strip()
            if not email or email.lower() in seen:
                continue
            seen.add(email.lower())
            if _NOREPLY_RE.search(email):
                noreply.append(email)
            else:
                real.append(email)

    return real, noreply


async def _get_json(http: httpx.AsyncClient, url: str):
    try:
        resp = await http.get(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug("GitHub request failed (%s): %s", url, exc)
        return None

    if resp.status_code == 404:
        return None
    if resp.status_code == 403:
        # Rate limit or abuse-detection. Caller should soft-skip.
        logger.debug("GitHub 403 on %s — likely rate-limited", url)
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None
