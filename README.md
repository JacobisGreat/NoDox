# NoDox

Personal OSINT self-audit tool scaffold (Step 1 + Step 2 complete):

- Full-stack project structure in place
- Meta Instagram OAuth gate fully wired
- Hard gate enforced: no pipeline access until backend successfully calls `GET /me/media`
- Ephemeral in-memory session storage only (no database)

## Folder Structure

```text
NoDox/
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   └── app/
│       ├── __init__.py
│       ├── main.py
│       ├── api/
│       │   ├── __init__.py
│       │   └── routes/
│       │       ├── __init__.py
│       │       ├── auth.py
│       │       ├── health.py
│       │       └── session.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── dependencies.py
│       │   └── session_store.py
│       ├── models/
│       ├── orchestration/
│       │   └── __init__.py
│       ├── pipelines/
│       │   ├── __init__.py
│       │   ├── README.md
│       │   ├── geolocation/
│       │   │   └── __init__.py
│       │   ├── identity/
│       │   │   └── __init__.py
│       │   └── web_footprint/
│       │       └── __init__.py
│       ├── schemas/
│       │   ├── __init__.py
│       │   └── auth.py
│       └── services/
│           ├── __init__.py
│           └── meta_oauth.py
├── frontend/
│   ├── .env.example
│   ├── index.html
│   ├── package.json
│   ├── postcss.config.js
│   ├── tailwind.config.js
│   ├── tsconfig.app.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── index.css
│       ├── main.tsx
│       ├── types.ts
│       ├── api/
│       │   └── client.ts
│       ├── components/
│       │   ├── AuthGatePanel.tsx
│       │   ├── ExposurePanel.tsx
│       │   └── PipelineStatusPanel.tsx
│       ├── features/
│       │   ├── findings/
│       │   ├── pipelines/
│       │   └── remediation/
│       └── layout/
├── .gitignore
└── README.md
```

## OAuth Gate Flow

1. Frontend calls `GET /api/auth/meta/start` and receives the Meta authorize URL.
2. User authenticates with Instagram/Meta.
3. Meta redirects to backend callback: `GET /api/auth/meta/callback`.
4. Backend exchanges code for token.
5. Backend calls `GET /me` and then `GET /me/media`.
6. Session is marked `gate_passed=true` **only if** `/me/media` succeeds.
7. Frontend displays gate status and locks pipelines until gate is passed.

## Backend Endpoints (Current)

- `GET /api/health`
- `GET /api/session`
- `GET /api/auth/meta/start`
- `GET /api/auth/meta/callback`
- `POST /api/auth/logout`
- `GET /api/me/media` (returns 403 unless gate passed)

## Run Locally

### 1) Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2) Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Frontend default URL: `http://localhost:5173`  
Backend default URL: `http://localhost:8000`

## Required Meta App Config

- OAuth redirect URI must include: `http://localhost:8000/api/auth/meta/callback`
- Scopes expected by this build: `instagram_graph_user_profile,instagram_graph_user_media`
- Provide `META_CLIENT_ID` and `META_CLIENT_SECRET` in `backend/.env`
