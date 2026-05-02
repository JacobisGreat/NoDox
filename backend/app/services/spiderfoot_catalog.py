"""SpiderFoot-derived dork patterns.

Translated from public-source modules in ``systems/spiderfoot/modules/``
(MIT). The runner is *not* ported — these are pure query templates that
we feed through the existing Serper search backend in
``app/pipelines/web_footprint.py``.

Module sources:
- sfp_pastebin.py        → paste-site site: dorks
- sfp_grep_app.py        → code-search dorks for credential leaks
- sfp_searchcode.py      → cross-repo code mention dorks
- sfp_emailformat.py     → email-format inference dorks
- sfp_filemeta.py        → document-metadata leaks
- sfp_leakdb / sfp_h1nobbdde / sfp_grayhatwarfare → leak archives

These complement ``dork_catalog`` (translated from DorkER) — there is
deliberate overlap on the obvious patterns; ``web_footprint`` de-dupes
case-insensitively before running queries.
"""

from __future__ import annotations


# Sites that historically host pastes and credential leaks. Sourced from
# the leak-aware spiderfoot modules; the small ones rotate through these
# domains rather than relying on Pastebin alone.
_PASTE_SITES: tuple[str, ...] = (
    "pastebin.com",
    "ghostbin.co",
    "rentry.co",
    "hastebin.com",
    "controlc.com",
    "justpaste.it",
    "0bin.net",
    "paste.ee",
    "pastes.io",
)

# Public code-host platforms. sfp_grep_app + sfp_searchcode + sfp_github
# focus on these. Keep the list short — every entry is a Serper query.
_CODE_SITES: tuple[str, ...] = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "gist.github.com",
    "sourcehut.org",
    "codeberg.org",
)

# Document mirrors / file-meta exposure (sfp_filemeta).
_DOC_SITES: tuple[str, ...] = (
    "scribd.com",
    "academia.edu",
    "researchgate.net",
    "slideshare.net",
)

# People-search aggregators that index teaser pages — a logged-out
# Google snippet often exposes the confirmed full name + city + age range,
# even when the live page asks for payment. Sourced from sfp_pgp +
# sfp_company analogues; trimmed to the ones that index reliably.
_AGGREGATOR_SITES: tuple[str, ...] = (
    "zoominfo.com",
    "spokeo.com",
    "beenverified.com",
    "rocketreach.co",
    "fastpeoplesearch.com",
    "whitepages.com",
    "truepeoplesearch.com",
    "thatsthem.com",
    "radaris.com",
    "mylife.com",
)

# Public records hosts — court filings, dockets, judgements. Coverage
# varies by jurisdiction; these four index in Google reliably enough that
# a single dork sweep is worth the budget.
_PUBLIC_RECORDS_SITES: tuple[str, ...] = (
    "courtlistener.com",
    "casetext.com",
    "judyrecords.com",
    "unicourt.com",
)

# WHOIS reverse-lookup mirrors — given an email, find domains registered
# to it. Public Google-indexed result pages are the only free way to do
# this without an API key.
_WHOIS_REVERSE_SITES: tuple[str, ...] = (
    "viewdns.info",
    "domainwat.ch",
    "domainbigdata.com",
    "whoxy.com",
)


def dorks_for_username(username: str) -> list[str]:
    """SpiderFoot-flavored username dorks.

    Skips the bare ``"{username}"`` query because both ``dork_catalog``
    and ``web_footprint._base_queries`` already issue it."""
    if not username:
        return []
    queries: list[str] = []

    # Paste-site sweep — sfp_pastebin equivalent across multiple hosts.
    for site in _PASTE_SITES:
        queries.append(f'site:{site} "{username}"')

    # Code-search sweep — sfp_grep_app / sfp_searchcode equivalent.
    for site in _CODE_SITES:
        queries.append(f'site:{site} "{username}"')

    # Credential / token leak shapes (sfp_grep_app's typical hit pattern).
    queries.extend(
        [
            f'"{username}" "api_key"',
            f'"{username}" "secret"',
            f'"{username}" "token"',
            f'"{username}" "BEGIN PRIVATE KEY"',
            f'"{username}" inurl:.env',
            f'"{username}" inurl:config',
        ]
    )

    return queries


def dorks_for_email(email: str) -> list[str]:
    """SpiderFoot-flavored email dorks.

    Designed to extend ``dork_catalog.dorks_for_email`` with leak-archive
    and code-search shapes. Caller is responsible for de-duplication."""
    if not email or "@" not in email:
        return []

    queries: list[str] = []

    # Paste-site sweep.
    for site in _PASTE_SITES:
        queries.append(f'site:{site} "{email}"')

    # Code-host sweep — common pattern in sfp_grep_app.
    for site in _CODE_SITES:
        queries.append(f'site:{site} "{email}"')

    # Doc-meta exposure (sfp_filemeta).
    queries.extend(
        [
            f'"{email}" filetype:doc',
            f'"{email}" filetype:docx',
            f'"{email}" filetype:xlsx',
            f'"{email}" filetype:pptx',
        ]
    )

    # WHOIS reverse — find domains historically registered with this
    # email. The aggregators below all index Google-reachable result
    # pages even when their live pages gate behind a captcha/login.
    for site in _WHOIS_REVERSE_SITES:
        queries.append(f'site:{site} "{email}"')

    return queries


def dorks_for_full_name(full_name: str) -> list[str]:
    """SpiderFoot-flavored full-name dorks — biographies, document
    mirrors, professional registries (sfp_filemeta + sfp_company),
    people-search aggregators, and public-records hosts."""
    if not full_name:
        return []
    queries: list[str] = []
    for site in _DOC_SITES:
        queries.append(f'site:{site} "{full_name}"')
    # People-search aggregator teasers — Google snippets often surface
    # confirmed name + location + age range from these pages even when
    # the live page is paywalled.
    for site in _AGGREGATOR_SITES:
        queries.append(f'site:{site} "{full_name}"')
    # Public records / court filings.
    for site in _PUBLIC_RECORDS_SITES:
        queries.append(f'site:{site} "{full_name}"')
    queries.extend(
        [
            f'"{full_name}" "court records"',
            f'"{full_name}" "voter"',
            f'"{full_name}" "obituary"',
            f'"{full_name}" "donor"',
        ]
    )
    return queries
