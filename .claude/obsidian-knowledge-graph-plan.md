# Obsidian-Style Knowledge Graph Plan

## Goal

Add a final, bottom-of-dashboard "knowledge web" that feels like Obsidian's graph view and summarizes the entire audit across:

- profile snapshot
- identity findings
- geolocation findings
- web footprint findings
- final aggregator summary
- remediation priorities

This graph should help the user see how evidence connects, not just read a linear list of findings.

## Recommended Stack

### Frontend

- `react-force-graph-2d`
  - Best fit for an Obsidian-like network view.
  - Gives pan, zoom, node dragging, force layout, and canvas rendering without building physics/layout from scratch.
- Existing `React 18 + TypeScript + Vite`
  - Keep the feature inside the current app structure.
- Existing `Tailwind CSS`
  - Use the current NODOXX terminal styling for the wrapper, legend, controls, and detail panel.
- Existing `framer-motion`
  - Optional for panel reveal and graph mount transitions, not for graph physics.

### Backend

- Keep `FastAPI` + current SSE flow.
- Reuse the existing aggregator pass in `backend/app/services/aggregator.py`.
- Do not add a new transport unless needed. Prefer extending the final `aggregator_done` payload or deriving the graph client-side from already streamed findings.

## Why This Fits This Repo

- The frontend already centralizes all audit state in `frontend/src/hooks/useAudit.ts`.
- The dashboard already has a natural final-summary surface in `frontend/src/components/AuditDashboard.tsx`.
- Findings already arrive in a structured format and are grouped by pipeline.
- The backend already emits a final `aggregator_done` event after all pipelines complete.
- `CLAUDE.md` explicitly warns that the SSE schema is a locked contract, so the safest first version is a frontend-derived graph.

## Recommendation

Build this in two phases.

### Phase 1: frontend-derived graph (recommended first ship)

Construct the graph entirely from:

- `profile`
- `findingsByPipeline`
- `aggregator`

This avoids changing the SSE event schema and gets the feature on screen quickly.

### Phase 2: backend-enriched semantic graph

Once the visualization is working, optionally extend the aggregator response with a graph-specific summary payload for better clustering, labels, and stronger "summary of everything" behavior.

## Data Model

Use a simple graph contract in the frontend first:

```ts
export interface KnowledgeNode {
  id: string;
  label: string;
  kind:
    | "profile"
    | "pipeline"
    | "finding"
    | "location"
    | "platform"
    | "url"
    | "identity"
    | "remediation"
    | "summary";
  pipeline?: "identity" | "geolocation" | "web_footprint";
  risk?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  score?: number;
  detail?: string;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeLink {
  source: string;
  target: string;
  kind:
    | "owns"
    | "contains"
    | "suggests"
    | "references"
    | "located_at"
    | "matches"
    | "summarizes";
  weight?: number;
}
```

## Graph Construction Rules

### Root nodes

- One `profile` node for the audited account.
- One `summary` node for the final aggregator summary.
- One `pipeline` node each for `identity`, `geolocation`, and `web_footprint`.

### Finding nodes

- Create one `finding` node per finding.
- Connect each finding to its pipeline node.
- Encode severity with color and node size.

### Entity nodes

Extract lightweight entities deterministically from `finding.metadata` and `finding.evidence_chain`:

- geolocation:
  - `location_name`
  - `city`
  - `region`
  - `country`
  - `lat` / `lon`
- identity:
  - matched platform/site names
  - usernames
  - display names
- web footprint:
  - domains
  - URLs
  - mention sources

Connect finding nodes to those entity nodes. This is what creates the "web" instead of just a starburst.

### Remediation nodes

- Create one node per remediation item from `aggregator.remediation_list`.
- Connect remediation nodes to the summary node.
- When possible, also connect a remediation node to the findings whose `remediation` text matches or overlaps.

## UI Shape

Add a new bottom section under the existing pipeline columns:

1. `KnowledgeGraphPanel`
   - section shell
   - title
   - legend
   - filter controls
   - selected-node detail panel

2. `KnowledgeGraphCanvas`
   - wraps `react-force-graph-2d`

3. `knowledgeGraph.ts`
   - pure builder utilities that convert audit state into graph nodes/links

### Placement

Put the graph below:

- exposure score
- geolocation map
- pipeline columns

This keeps the graph as the final "everything connected" summary view.

## Visual Direction

Aim for "Obsidian graph view meets NODOXX terminal UI":

- dark background matching the current dashboard
- fine, dim link lines
- brighter high-risk nodes
- subtle pulse on the active node
- node colors by type:
  - profile: white
  - identity: amber
  - geolocation: cyan
  - web footprint: red
  - remediation: green
  - summary: neutral bright

Keep it intentional, not decorative. The graph is an analytic surface.

## Interaction Model

- zoom + pan
- drag nodes
- click a node to open a side detail panel
- hover to highlight immediate neighbors
- filter toggles:
  - all
  - identity
  - geolocation
  - web footprint
  - remediation
  - high risk only
- "recenter" control
- "freeze layout" control once the graph settles

## Implementation Steps

### Step 1: add dependencies

Frontend:

```bash
npm install react-force-graph-2d
```

Only add more graph libraries if `react-force-graph-2d` proves limiting.

### Step 2: add graph types

Update `frontend/src/types.ts` with:

- `KnowledgeNode`
- `KnowledgeLink`
- `KnowledgeGraphData`

Do not disturb existing audit event types.

### Step 3: add graph builder utilities

Create:

- `frontend/src/utils/knowledgeGraph.ts`

Responsibilities:

- build graph from `profile`, `findingsByPipeline`, and `aggregator`
- normalize IDs
- dedupe shared entity nodes
- assign node kinds, sizes, colors, weights
- expose helper filters

### Step 4: add UI components

Create:

- `frontend/src/components/KnowledgeGraphPanel.tsx`
- `frontend/src/components/KnowledgeGraphCanvas.tsx`

Responsibilities:

- render the section shell
- own selected-node state
- own filter state
- pass graph data into the canvas
- render detail card for clicked node

### Step 5: mount it in the dashboard

Update:

- `frontend/src/components/AuditDashboard.tsx`

Render the graph only when one of these is true:

- findings exist
- aggregator exists

Prefer rendering it after the pipeline grid so it feels like the final synthesis layer.

### Step 6: phase-2 backend enrichment (optional)

If Phase 1 feels too literal, extend the aggregator output shape with:

```json
{
  "knowledge_graph": {
    "clusters": [],
    "summary_nodes": [],
    "summary_links": []
  }
}
```

If you do this:

- update `backend/app/services/aggregator.py`
- update `backend/app/schemas/events.py`
- update `frontend/src/types.ts`
- update `frontend/src/hooks/useAudit.ts`

Do not add a brand-new SSE event unless there is a strong reason. Reuse `aggregator_done`.

## File Plan

### New files

- `frontend/src/components/KnowledgeGraphPanel.tsx`
- `frontend/src/components/KnowledgeGraphCanvas.tsx`
- `frontend/src/utils/knowledgeGraph.ts`

### Likely edited files

- `frontend/package.json`
- `frontend/src/components/AuditDashboard.tsx`
- `frontend/src/types.ts`
- `frontend/src/index.css` if graph-specific utility styles are needed

### Optional backend files for Phase 2

- `backend/app/services/aggregator.py`
- `backend/app/schemas/events.py`

## Acceptance Criteria

- A new bottom-of-dashboard knowledge graph renders without breaking the current audit flow.
- The graph is built from all three pipelines plus the final summary/remediation data.
- Clicking a node explains why it exists and what evidence supports it.
- Shared entities create visible cross-links between findings.
- High-risk nodes are immediately visually obvious.
- The graph remains usable on laptop screens and does not tank performance.
- The Phase 1 version works without changing the existing SSE contract.

## Risks

- A force graph can become noisy if every finding becomes its own equal-weight node.
  - Mitigation: collapse duplicate entities and cap low-value nodes.
- Client-side entity extraction can be imperfect.
  - Mitigation: start deterministic and only add LLM-enriched clustering in Phase 2.
- Large graphs can feel chaotic.
  - Mitigation: add filters, freeze layout, and selected-node focus behavior.

## Strong Default Build Order For Claude

1. Add `react-force-graph-2d`.
2. Build a frontend-only graph generator from existing audit state.
3. Add the new bottom panel and interaction controls.
4. Tune styles and node/link semantics.
5. Only then consider backend enrichment through `aggregator_done`.

## Notes For Claude

- Trust `CLAUDE.md`, not `README.md`.
- The current README is stale.
- The event schema is a shared contract; avoid casual SSE changes.
- Keep the graph as a synthesis layer, not a replacement for the existing findings cards.
- Reuse the current aesthetic language instead of introducing a generic SaaS graph widget.
