<claude-mem-context>
# Memory Context

# [NoDox] recent context, 2026-05-02 11:19am EDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (17,540t read) | 642,240t work | 97% savings

### May 2, 2026
223 10:01a 🔵 NoDox Frontend Project Structure Mapped for Kali Linux Terminal Redesign
224 10:02a 🔵 NoDox Component Design Patterns Audited — Pre-Redesign Inventory Complete
225 10:08a ⚖️ NoDox Kali Terminal Redesign Plan Formalized — Token System and Per-Component Checklist
226 " ⚖️ Kali Terminal Redesign Plan Approved — Phase 2 Execution Unlocked
227 10:29a ✅ Project Renamed from "nodox" to "nodoxx"
228 " 🔵 NoDox → NodoXX Rename Scope: 30 Files, Mixed Casing
229 10:30a 🔵 Identity Pipeline Has 67% False Positive Rate Due to Sherlock's Weak Detection
230 " ✅ First Rename Edit Applied: IDENTITY_FALSE_POSITIVES.md Author Field
231 " ✅ IDENTITY_FALSE_POSITIVES.md Rename Edits: 4 of 6 Single-X Occurrences Replaced
232 10:32a ✅ IDENTITY_FALSE_POSITIVES.md Fully Renamed; README.md Title and Folder Reference Updated
233 " ✅ Rename Pass Extended to THEME.md and ATTRIBUTION.md
234 10:33a ✅ Env Var NODOX_GEOCLIP_DEVICE Renamed to NODOXX_GEOCLIP_DEVICE in .env.example
235 10:54a ✅ Git pull added new maps.md file to NoDox repo
236 " ⚖️ maps.md documents migration plan from Leaflet to MapLibre GL with 3D globe
237 10:56a 🔵 NoDox Frontend Structure: AuditDashboard with Three Pipeline Tabs
239 " 🔵 FindingCard Expects Structured Finding Objects with Specific Schema
242 " 🔵 web_footprint.py Backend Pipeline: Full Architecture and Potential Raw Data Dump Root Cause
238 " 🔵 GeoMap.tsx full implementation audit: Leaflet-based with browser Nominatim geocoding
241 " 🔵 GeoMap integration scope: single consumer in AuditDashboard, receives geolocation pipeline findings
245 " 🔵 NoDox frontend src tree fully mapped: 18 files, hooks-based architecture
240 10:57a 🔵 Frontend package.json confirms maplibre-gl and three.js not yet installed
243 10:58a 🔴 Fixed AI Prompt Quality for web_footprint Triage and PII Extraction
247 " 🔵 No local GeoJSON or world border assets exist in NoDox repo
244 " 🔴 Added _clean_one_liner() Defensive Sanitizer Applied to All AI Response Fields
248 " ✅ Downloaded Natural Earth 110m land TopoJSON to frontend/public for self-hosted globe rendering
S114 Fix NoDox web_footprint third tab raw AI data dump — fixes complete, dev servers live and verified (May 2, 10:58 AM)
246 10:59a 🔵 Frontend root contains THEME.md and pre-built dist/ directory
249 " 🔵 topojson-client@3 has no CLI binary — npx conversion approach fails
250 11:00a 🟣 TopoJSON converted to GeoJSON via Node script — world-land-110m.geojson ready as static asset
251 " ⚖️ Chose pure three.js globe over MapLibre — building standalone Globe.tsx component
252 " ⚖️ Three-task migration plan defined: install three.js → build Globe.tsx → wire into GeoMap.tsx
253 11:01a ⚖️ Final four-task migration plan locked; Task 1 (install three.js) now in progress
254 " ✅ three.js@0.171.0 installed; source land-110m.json removed, keeping only converted GeoJSON
255 " ✅ package.json updated: three@^0.171.0 added alongside still-present Leaflet deps; Task 2 Globe.tsx build starting
256 " 🟣 Globe.tsx created: full three.js WebGL globe with land outlines, markers, centroid pulse rings, and hover tooltips
S115 Fix NoDox web_footprint third tab raw AI data dump — all fixes shipped, dev servers live at :8000 and :5175 (May 2, 11:02 AM)
S118 NoDox Leaflet → three.js globe migration: dev server confirmed running at localhost:5176 (May 2, 11:03 AM)
257 11:05a 🟣 GeoMap.tsx migrated from Leaflet to Globe component — Task 3 complete, Task 4 starting
S116 Fix NoDox web_footprint third tab raw AI data dump — fixes complete, dev servers live and verified healthy (May 2, 11:07 AM)
S117 NoDox frontend: Leaflet → three.js WebGL globe migration (triggered by "pull from github") (May 2, 11:07 AM)
S119 NoDox three.js globe migration complete; stale dev server processes cleaned up (May 2, 11:08 AM)
258 11:10a 🔵 NoDox Frontend Tech Stack Identified
S120 NoDox frontend: Leaflet → three.js WebGL globe migration (triggered by "pull from github"); dev server live at localhost:5176 (May 2, 11:11 AM)
266 11:11a 🔴 Missing react-force-graph-2d dependency installed
267 " 🔐 Live Instagram session cookies and Serper API key shared in plaintext
268 " 🔵 Backend credential configuration scope identified
259 " ✅ Replaced Leaflet Map Library with Three.js for Knowledge Graph
S121 Implement Obsidian-style knowledge graph for NoDox frontend per .claude/obsidian-knowledge-graph-plan.md (May 2, 11:11 AM)
260 " 🔵 @tweenjs/tween.js Added as Implicit Three.js Dependency
261 " 🔵 react-force-graph-2d Install Attempt Failed Silently — Pure Three.js Approach Confirmed
262 11:12a ✅ react-force-graph-2d Successfully Installed Alongside Three.js
264 " 🟣 KnowledgeGraphCanvas Component Implemented with Full Custom Canvas Rendering
S122 Install new Instagram session cookies into backend .env and restart backend to clear 429 rate-limit errors (May 2, 11:12 AM)
263 11:13a 🔵 react-force-graph-2d API Surface Confirmed for Knowledge Graph
265 11:15a 🟣 NoDox Frontend Build Succeeds with Knowledge Graph Dependencies
269 11:17a ✅ Instagram session cookies rotated in backend .env
270 " 🔵 POST /api/profile/fetch returning 429 Too Many Requests
271 " 🔵 Backend process killed to force .env reload of new Instagram credentials
272 11:18a ✅ Backend restarted with new Instagram credentials now active
S123 Install new Instagram session cookies into backend .env and restart backend to clear 429 rate-limit errors (May 2, 11:19 AM)
**Investigated**: - backend/.env read; old IG_SESSIONID identified (account 70261353414)
    - backend/.env.example confirmed supported vars: IG_SESSIONID, IG_DS_USER_ID, IG_CSRFTOKEN
    - instaloader_fetch.py _session_cookies() (lines 175–186): only these three vars are consumed; 8 other browser cookies ignored
    - Backend autoreload log: confirmed 429s on POST /api/profile/fetch with old session
    - Both servers probed: http://127.0.0.1:8000 HTTP 200, http://localhost:5173/ HTTP 200

**Learned**: - uvicorn WatchFiles does NOT watch .env — full process kill+restart required to pick up new credentials
    - Bash-wrapped powershell corrupts PID arguments via extglob expansion; native PowerShell must be used for port-kill operations on this machine
    - Old IG session (account 70261353414) was causing 429 rate-limit responses; new session (account 63738777181) should clear this
    - Only sessionid, ds_user_id, csrftoken matter for instaloader_fetch.py — other 8 browser cookies are unused
    - SERPER_API_KEY was already correctly set; no change needed
    - IG session cookie is tied to a personal account — logging out anywhere invalidates it

**Completed**: - backend/.env updated: IG_SESSIONID rotated to account 63738777181, IG_DS_USER_ID and IG_CSRFTOKEN added
    - Backend killed (PID 23624) and restarted fresh (reloader PID 2776, server PID 34056); HTTP 200 confirmed
    - Frontend (Vite) running on http://localhost:5173/ HTTP 200
    - react-force-graph-2d installed (35 packages); KnowledgeGraphCanvas.tsx import error resolved
    - All five web_footprint.py AI sanitizer fixes remain in place (1644 lines, syntax-verified)

**Next Steps**: Both servers confirmed live (8000 and 5173 both HTTP 200). Ready for end-to-end audit test: open http://localhost:5173/, run a profile fetch, verify no more 429 rate-limit errors, and confirm web_footprint third tab shows clean structured FindingCards.


Access 642k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>