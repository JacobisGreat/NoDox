# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Heads up: README.md is stale

`README.md` describes a Meta OAuth gate, an `api/routes/` + `core/` backend layout, and a `features/` frontend layout. **None of that is current.** The repo has been restructured to anonymous Instagram fetching via `instaloader`, a flat `routes/` layout, and a component-based frontend. Trust the code, not the README.

## Run

Backend (FastAPI on `:8000`):
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # fill ANTHROPIC_API_KEY, GOOGLE_CSE_API_KEY, GOOGLE_CSE_CX
uvicorn app.main:app --reload --port 8000
```

Frontend (Vite/React on `:5173`):
```bash
cd frontend
npm install
npm run dev          # dev server
npm run build        # tsc -b && vite build
npm run preview      # preview built bundle
```

There is no test runner or linter wired up yet (no `pytest`/`vitest`/`eslint` scripts in `requirements.txt` or `package.json`). Type checking happens implicitly via `tsc -b` in the frontend build.

## Architecture

### One audit per session, all in-memory

`session_store.py` owns a process-global `SESSIONS: dict[str, SessionState]`. There is **no database, no disk persistence, no auth**. A `gc_loop` task in `main.py`'s lifespan evicts sessions older than `SESSION_TTL_MINUTES` (default 120). On shutdown, lifespan cancels every pipeline task and closes every per-session `httpx.AsyncClient`.

### Request flow

1. `POST /api/profile/fetch` (`routes/profile.py`) — calls `services/instaloader_fetch.fetch_profile_and_posts` anonymously, creates a `SessionState`, returns `session_id` + profile snapshot. Private profiles are 422; rate-limited 429; not-found 404.
2. Frontend navigates to `/audit/{session_id}`, mounts `AuditDashboard`, which calls `POST /api/audit/{id}/start` (via `useAudit`) and opens `EventSource` on `GET /api/audit/{id}/stream` (via `useSSE`).
3. `routes/audit.py:start_audit` lazy-builds per-session services (httpx client, `AnthropicClient`, `CostTracker`, `ImageDownloader`, semaphores), kicks off three pipeline tasks and one coordinator task. The coordinator awaits all three, runs the aggregator if any findings exist, then publishes `stream_done`.
4. `routes/stream.py` walks `session.events` from a cursor, blocking on `session.event_condition` between batches and emitting `: keepalive` frames every 15 s.

### The locked event schema (`schemas/events.py`)

Every SSE frame is `{type, data}`. Six event types — **do not invent new ones without updating both backend builders and the `useAudit` handler map**:

- `profile_loaded` — `{profile, post_count}`
- `pipeline_status` — `{pipeline, status, detail}` where `pipeline ∈ {identity, geolocation, web_footprint}` and `status ∈ {idle, running, complete, error, budget_exceeded}`
- `finding` — `{pipeline, finding}` where `finding` is the `Finding` dataclass (`source`, `evidence_chain`, `confidence`, `risk_level`, `remediation`, `metadata`)
- `cost_update` — `{cost}` (running USD total)
- `aggregator_done` — `{exposure_score, summary, remediation_list}`
- `stream_done` — terminal, the SSE iterator returns after this

### Three pipelines, one contract

Each pipeline is a `run(session)` coroutine in `app/pipelines/{identity,geolocation,web_footprint}.py`. The orchestrator (`routes/audit.py:PIPELINES`) treats them uniformly. A pipeline must:

- emit `pipeline_status("running")` on entry, `complete`/`error`/`budget_exceeded` on exit
- append every finding to `session.data["findings"]` **and** publish a `finding` event immediately (never buffer)
- swallow per-target exceptions — one flaky platform/site must not abort the pipeline
- check `cost_tracker.can_spend(scope, estimate)` before any Anthropic call and short-circuit to `budget_exceeded` if False

Pipeline specifics:
- **identity** — sweeps `app/data/platforms.json` (Sherlock-style), `Semaphore(30)`. Match scoring blends username equality, fuzzy display-name (`rapidfuzz`), perceptual profile-pic hash (`imagehash`), and lazy-loaded bio cosine similarity (`sentence-transformers`).
- **geolocation** — analyzes post images/captions for location signals; uses Anthropic vision via the shared `vision_semaphore`.
- **web_footprint** — Google Programmable Search → `trafilatura` extraction. Has its own scope budget (`WEB_FOOTPRINT_BUDGET_SHARE_USD`, default $0.35) and hard caps (`WEB_FOOTPRINT_MAX_QUERIES`, `WEB_FOOTPRINT_MAX_FULL_FETCHES`).

### Cost is a hard invariant

`services/cost_tracker.py` enforces `AUDIT_COST_CEILING_USD` (default $1.00) per audit, with optional per-scope sub-budgets. Pricing comes from env (`CLAUDE_SONNET_*`, `CLAUDE_HAIKU_*` per-million-token rates) and matches by exact model id then by `sonnet`/`haiku` substring. Every Anthropic call must record usage; pipelines must `can_spend` *before* spending. Crossing the ceiling means the pipeline emits `budget_exceeded` and stops — it does not throw.

### Two-instance development model

Comments like `# instance 2's web_footprint pipeline` in `config.py` and `routes/audit.py` are intentional. Identity and the orchestration scaffolding were authored by one Claude session ("instance 1"); geolocation and web_footprint by another ("instance 2"). The orchestrator imports all three uniformly so either instance can be missing during development without breaking the other's pipelines. **The locked event schema (`schemas/events.py`) and finding shape (`schemas/findings.py`) are the contract between instances** — changing them requires coordination across both pipeline implementations and the frontend `useAudit` hook.

### Frontend shape

- Routing is hand-rolled in `App.tsx` (regex against `window.location.pathname`); two routes only: `/` (`LandingPage`) and `/audit/{sessionId}` (`AuditDashboard`).
- `hooks/useSSE.ts` owns the `EventSource` lifecycle; `hooks/useAudit.ts` owns audit state and dispatches per event type. Adding a new event type means adding both a handler in `useAudit` and any required type in `types.ts`.
- `api.ts` is the only place that calls the backend (`fetchProfile`, `startAudit`). Vite is configured to proxy `/api/*` to `:8000` — see `vite.config.ts`.
- `findingsByPipeline` is keyed by the same three pipeline names as the backend — keep them in sync.
