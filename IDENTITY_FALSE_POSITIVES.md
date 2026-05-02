# Identity Cross-Reference False Positives — Research & Fix Plan

**Date:** 2026-05-02
**Author:** NoDox engineering
**Status:** Investigation complete; fix in progress

## TL;DR

The Identity Cross-Reference column is producing findings whose URLs do not
exist for the target user. Root cause: NoDox vendored Sherlock's
`data.json` (478 sites) and 321 of those sites use **status-code-only
detection with no `errorCode` list** — i.e. "is the response 2xx?" That
check is wrong on the modern web because:

- SPAs render "user not found" client-side after returning HTTP 200
- WAF/CDN challenge pages return HTTP 200 with bot-check HTML
- Login-walls return HTTP 200 redirecting to a sign-in flow

The fix is to switch from Sherlock's data.json to **WhatsMyName's
`wmn-data.json`** (the OSINT community has already converged on it for
exactly this reason) and use its **two-sided detection**: a "found" verdict
requires a positive marker (`e_code` AND `e_string` in body) *and* the
absence of the negative marker. Anything that matches neither side is
inconclusive, not a hit.

This single change kills the dominant FP class and aligns NoDox with what
`systems/blackbird/` already does internally.

---

## 1. Diagnosis: where the false positives come from

### 1.1 Current detection logic

`backend/app/pipelines/identity.py::_is_claimed` implements Sherlock's
three-mode matcher:

```python
for et in error_types:
    if et == "status_code":
        error_codes = platform.get("error_codes") or []
        if error_codes:
            if status_code in error_codes:
                return False
        elif not (200 <= status_code < 300):
            return False
    elif et == "message":
        if any(msg in body for msg in error_messages):
            return False
    elif et == "response_url":
        if not (200 <= status_code < 300):
            return False
return True
```

A platform is "claimed" when *every* declared `errorType` says so. For the
**321 status-code-only sites without an explicit `errorCode` list**, that
collapses to: *did the server return any 2xx?* — which is the entire
problem.

### 1.2 Distribution of detection methods in NoDox's vendored data

```
total sites:                                     478
errorType=status_code (no errorCode, no errorMsg): 321  ← weak
errorType=status_code (with errorCode list):       5
errorType=message:                                126
errorType=response_url:                            26
```

**67% of the catalog is the weak case.** Most identity findings the user
sees come from this class.

### 1.3 Five concrete failure modes

1. **Soft-200 SPAs.** Modern profile pages (Instagram, ArtStation, Anilist,
   1337x, Strava, Discord, ProductHunt, HackTheBox) return HTTP 200 with a
   shell HTML, then show "user not found" client-side after a JS hydration.
   To a status-code matcher every URL exists. Documented examples:
   - sherlock-project/sherlock #2313 — fake username matched on Amino,
     Bikemap, Discord, HackTheBox, HudsonRock, Kick, LibraryThing,
     ProductHunt, Strava simultaneously.
   - sherlock-project/sherlock #2815 — 1337x.to dropped its `<title>404 Not
     Found</title>` marker but kept returning 200.
   - sherlock-project/sherlock #2750 — Instagram FP.

2. **WAF/CDN challenge pages return 200.** Cloudflare Turnstile, AWS WAF,
   PerimeterX, DataDome all front-load an interstitial that returns HTTP 200
   with a body like `<title>Just a moment...</title>` or
   `AwsWafIntegration.forceRefreshToken`. Sherlock #541 cites Yandex
   captchas as a chronic source. Cloudflare's own
   [bot-score docs](https://developers.cloudflare.com/bots/concepts/bot-score/)
   confirm headless / non-browser clients are auto-flagged — exactly
   NoDox's traffic profile.

3. **`follow_redirects=True` masks "not found."** httpx defaults
   `follow_redirects=False` but our identity probe sets it `True`. Sites
   that 30x to homepages (Aptoide), login pages (LinkedIn), or generic
   error landing pages turn into 200 responses by the time the matcher
   sees them. Sherlock #541 catalogues several.

4. **Stale data.json.** Sherlock's repo has a continuous tail of FP bug
   reports — issues #901, #959, #962, #1094, #1317, #1837, #2273, #2313,
   #2714, #2734, #2750, #2782, #2815, #2818 — most because sites silently
   changed routing or error markup. PR #2186 (Jun 2024) had to *remove*
   Zhihu rather than fix it. NoDox's vendored copy ages every day it
   isn't refreshed.

5. **Username collisions.** The Instagram-derived handle is one person on
   Instagram and a completely unrelated person on Strava or LibraryThing.
   Even a structurally-correct check answers "does *some* user with that
   handle exist?" not "does *this* user exist?" Without cross-validating
   the hit against display name / photo / bio, every correct hit is
   still an identity-level FP candidate.

---

## 2. What other tools do better

### 2.1 WhatsMyName (`systems/blackbird/` consumes this; it's the convergence point)

[github.com/WebBreacher/WhatsMyName](https://github.com/WebBreacher/WhatsMyName)

The schema each site has:

| Field | Meaning |
|---|---|
| `e_code` | HTTP code that, **combined with** `e_string`, means "user found" |
| `e_string` | substring that **must appear** in body for "user found" |
| `m_code` | HTTP code that means "user missing" |
| `m_string` | substring whose **presence** confirms "user missing" |
| `known` | array of verified-existing usernames (for self-test) |
| `headers`, `post_body` | when the site needs auth headers / POST |

A verdict requires **both sides** to agree:
- **Found**: `e_code == status` AND `e_string in body` AND `m_string NOT in body`
- **Missing**: `m_code == status` OR `m_string in body`
- **Anything else → inconclusive, do not emit a finding.**

Key project facts:
- Updated **daily** by community PRs.
- ~600+ sites at last audit.
- Self-test fixture per site (`known`) means the community can detect
  rotted entries automatically.
- WhatsMyName itself **stopped shipping a checker** in May 2023; it's
  pure data now. Every modern OSINT tool that takes this seriously
  (blackbird, recon-ng plugins) consumes it.

### 2.2 maigret (sherlock fork that solved this in 2020)

[github.com/soxoj/maigret](https://github.com/soxoj/maigret)

Same idea, different field names: `presenseStrs` and `absenceStrs` per
site, plus a `protection` flag for Cloudflare/CAPTCHA-fronted entries.
~3000 sites covered. The published
[comparison](https://footprintiq.app/compare/maigret-vs-sherlock) names
"less false positives" as maigret's headline differentiator. We don't
need maigret itself — just the architectural lesson.

### 2.3 blackbird (already vendored at `systems/blackbird/`)

`systems/blackbird/src/modules/core/username.py:64` is doing exactly
this:

```python
if (site["e_string"] in response["content"]) and (
    site["e_code"] == response["status_code"]
):
    if (site["m_string"] not in response["content"]) and (
        (site["m_code"] != response["status_code"])
        if site["m_code"] != site["e_code"]
        else True
    ):
        returnData["status"] = "FOUND"
```

Two-sided. Confirms our chosen architecture is field-tested.

### 2.4 socialscan — the orthogonal approach

[github.com/iojw/socialscan](https://github.com/iojw/socialscan) doesn't
scrape profile pages at all. It hits each platform's *registration
availability endpoint* (the API the sign-up form uses) with proper CSRF
tokens and asks "is this username available?" Coverage is small (~13
platforms including Instagram), but for the platforms it covers it's
near ground-truth. Out of scope for this fix but worth noting for a
future tier-1 boost on Instagram/GitHub/Reddit.

---

## 3. Sherlock issue tracker themes (2024–2026)

Read the issue tracker; the same five themes recur:

| # | Theme | Representative issues |
|---|---|---|
| 1 | Soft-200 SPAs and dynamic errors | #2313, #2815, #2273, #2714, #2734, #2750, #2782, #2818 |
| 2 | Maintainers removing rather than fixing entries | PR #2186 (Zhihu deleted) |
| 3 | "Lots of FPs at once" reports | #1094, #962 |
| 4 | Redirects masking "not found" (Aptoide, Yandex captcha) | #541 |
| 5 | Sites that return identical bodies for all usernames | tldrlegal note (Aug 2024) — unfixable |

The maintainers themselves don't have a structural answer.

---

## 4. Fix plan — ordered by ROI

### Tier 1 — must do (~80% of FP reduction)

**1. Replace `sherlock_data.json` with `wmn-data.json`.**
   Source: `https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json`.
   Vendor it as `backend/app/data/wmn-data.json`. Rewrite
   `loader.py::load_platforms()` to normalize WMN's schema into the
   pipeline's existing shape, **carrying `e_string` / `e_code` /
   `m_string` / `m_code` through as first-class fields** instead of
   collapsing them into Sherlock's coarse `error_types` list.

**2. Two-sided detection in `_is_claimed`.** Rewrite to:
   ```
   FOUND   if  e_code == status AND e_string in body AND m_string NOT in body
   MISSING if  m_code == status OR  m_string in body
   else        INCONCLUSIVE  → return None (never emit finding)
   ```
   Same change applies to `account_probe.py::_check_one`.

### Tier 2 — high-leverage (~10% additional reduction)

**3. Strengthen WAF/CDN detection.** Today we have 4 fingerprints. Add
   the modern set:
   - Cloudflare: `cf-mitigated` header, `__cf_bm` Set-Cookie,
     `<title>Just a moment...</title>`, `cf-chl-` script paths
   - AWS WAF: `x-amzn-waf-action` header, `awswaf-token`
   - PerimeterX / Human: `_pxhd` cookie, `px-captcha`
   - DataDome: `x-datadome-cid`, `geo.captcha-delivery.com`

   On any match, mark the response **inconclusive** — never emit it as a
   finding.

**4. Disable `follow_redirects` on status-code probes.** Set
   `follow_redirects=False` for any site whose detection is purely
   status-code-driven. Treat any 30x as "user missing." Keep redirects
   enabled only for `message`-type sites that genuinely need the final
   page body.

### Tier 3 — polish (worth doing once Tier 1+2 ships)

**5. Confidence floor.** When the only positive signal is a status code
   (no body match, no display-name agreement with the Instagram seed),
   emit at LOW confidence rather than MEDIUM. Today `_risk_for` already
   awards MEDIUM at 0.55 confidence — make sure status-code-only hits
   stay below that threshold.

**6. Cross-validate via existing aggregator.** The geolocation pipeline
   already runs Sonnet for cross-evidence reconciliation. Identity
   findings should pass through the same logic so a status-code-only
   "hit" gets dropped if the name/photo/bio scores all come back zero.

### Out of scope (for now)

- **Per-run self-test using `known` usernames.** Cheap and elegant but
  +600 requests per audit and Tier 1 already gets us most of the way.
- **socialscan-style registration probes for top platforms.** Compelling
  for Instagram/GitHub/Reddit specifically, but breaks when sites change
  signup APIs — net win, more maintenance.

---

## 5. Implementation sequence (the actual fix)

1. Download `wmn-data.json` to `backend/app/data/wmn-data.json`.
2. Rewrite `backend/app/data/loader.py` to read WMN format. Keep
   `load_platforms()` returning the same shape as today plus four new
   fields: `e_code`, `e_string`, `m_code`, `m_string`. Drop or alias the
   old `error_types` / `error_messages` / `error_codes` /
   `error_url_template` derived fields so existing call-sites continue
   to work during the cutover.
3. Update `backend/app/pipelines/identity.py::_is_claimed` to the
   two-sided check.
4. Update `backend/app/services/account_probe.py::_check_one` similarly.
5. Add the expanded WAF fingerprint set in `identity.py`.
6. Set `follow_redirects=False` on the status-code-only path.
7. Update `backend/tests/test_identity_pipeline.py` for the new schema
   and a new test asserting two-sided semantics.
8. Smoke-test with a known-fake username (`thisuserdoesnotexistxx9999`)
   and confirm zero or near-zero hits across 600+ platforms.
9. Smoke-test with a known-good username and confirm hits on the
   expected major platforms.

`backend/app/data/sherlock_data.json` becomes legacy — keep it for one
release in case we need to roll back, then delete in the cleanup pass.

---

## 6. Sources

- [Sherlock Issue tracker](https://github.com/sherlock-project/sherlock/issues)
- [Sherlock #2313 — Long fake username has many false positives](https://github.com/sherlock-project/sherlock/issues/2313)
- [Sherlock #2815 — False positive for 1337x.to](https://github.com/sherlock-project/sherlock/issues/2815)
- [Sherlock #541 — False positives (redirects/captcha)](https://github.com/sherlock-project/sherlock/issues/541)
- [Sherlock #2750 — Instagram false positive](https://github.com/sherlock-project/sherlock/issues/2750)
- [Sherlock PR #2186 — Removed broken entries](https://github.com/sherlock-project/sherlock/pull/2186)
- [WhatsMyName project](https://github.com/WebBreacher/WhatsMyName)
- [WhatsMyName JSON schema](https://github.com/WebBreacher/WhatsMyName/blob/main/wmn-data-schema.json)
- [maigret](https://github.com/soxoj/maigret)
- [socialscan](https://github.com/iojw/socialscan)
- [Maigret vs Sherlock comparison](https://footprintiq.app/compare/maigret-vs-sherlock)
- [Cloudflare bot-score documentation](https://developers.cloudflare.com/bots/concepts/bot-score/)
- [Cloudflare WAF false-positive troubleshooting](https://developers.cloudflare.com/waf/troubleshooting/fake-bot-managed-rules/)
- `systems/blackbird/src/modules/core/username.py` — reference two-sided implementation
