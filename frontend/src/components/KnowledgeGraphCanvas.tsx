import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
} from "react";
import ForceGraph2D, {
  ForceGraphMethods,
  LinkObject,
  NodeObject,
} from "react-force-graph-2d";
import {
  KnowledgeGraphData,
  KnowledgeLink,
  KnowledgeLinkKind,
  KnowledgeNode,
  KnowledgeNodeKind,
  PipelineName,
  RiskLevel,
} from "../types";
import {
  ANCHOR_IDS,
  PROFILE_NODE_ID,
  SUMMARY_NODE_ID,
  linkKindLabel,
  nodeIdOf,
  parentNodeId,
  pipelineNodeId,
} from "../utils/knowledgeGraph";

export interface KnowledgeGraphCanvasHandle {
  recenter: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

type ForcedNode = NodeObject<KnowledgeNode>;
type ForcedLink = LinkObject<KnowledgeNode, KnowledgeLink>;

interface Props {
  data: KnowledgeGraphData;
  width: number;
  height: number;
  selectedId: string | null;
  hoverId: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
}

const COLOR_TEXT = "#FFFFFF";
const COLOR_DIM = "#A1A1A1";
const COLOR_LABEL = "#666666";
const COLOR_BORDER = "#1F1F1F";
const COLOR_BG = "#000000";

// Profile sits at the center, identity/web flank it, geolocation anchors the
// top, summary anchors the bottom. Pipelines own vertical strips below the
// midline so finding clouds don't bleed into each other.
const ANCHOR_POSITIONS: Record<string, { x: number; y: number }> = {
  [PROFILE_NODE_ID]: { x: 0, y: -110 },
  [pipelineNodeId("geolocation")]: { x: 0, y: -380 },
  [pipelineNodeId("identity")]: { x: -420, y: -110 },
  [pipelineNodeId("web_footprint")]: { x: 420, y: -110 },
  [SUMMARY_NODE_ID]: { x: 0, y: 380 },
};

// Each pipeline owns a strip its findings/entities are pulled toward.
// identity (left) and web (right) hang down from y=-110 toward y=80.
// geolocation now anchors the top (y=-380), so its findings drift further
// upward (y=-520) to avoid piling on top of the profile node in the center.
const PIPELINE_STRIP_X: Record<PipelineName, number> = {
  identity: -420,
  geolocation: 0,
  web_footprint: 420,
};
const PIPELINE_STRIP_Y: Record<PipelineName, number> = {
  identity: 80,
  geolocation: -520,
  web_footprint: 80,
};

function riskBrightness(risk?: RiskLevel): string {
  if (risk === "CRITICAL" || risk === "HIGH") return COLOR_TEXT;
  if (risk === "MEDIUM") return COLOR_DIM;
  if (risk === "LOW") return COLOR_LABEL;
  return COLOR_LABEL;
}

function nodeRadius(kind: KnowledgeNodeKind): number {
  switch (kind) {
    case "profile":     return 17;
    case "summary":     return 15;
    case "pipeline":    return 13;
    case "finding":     return 8;
    case "remediation": return 7;
    case "location":    return 6.5;
    case "platform":    return 6;
    case "url":         return 6;
    case "identity":    return 6.5;
    default:            return 6;
  }
}

// Collision is computed by the lib as sqrt(nodeVal) * nodeRelSize, so we map
// nodeVal ≈ radius * 1.4 to keep visual + collision proportional.
function nodeVal(kind: KnowledgeNodeKind): number {
  return Math.max(2, nodeRadius(kind) * 1.4);
}

function linkStyle(kind: KnowledgeLinkKind): {
  color: string;
  width: number;
  dash: number[] | null;
} {
  switch (kind) {
    case "owns":
      return { color: COLOR_TEXT, width: 1.1, dash: null };
    case "summarizes":
      return { color: COLOR_DIM, width: 0.95, dash: null };
    case "contains":
      return { color: COLOR_DIM, width: 0.75, dash: null };
    case "suggests":
      return { color: COLOR_DIM, width: 0.75, dash: null };
    case "located_at":
    case "references":
      return { color: COLOR_LABEL, width: 0.55, dash: null };
    case "matches":
      return { color: COLOR_DIM, width: 0.55, dash: [4, 3] };
    default:
      return { color: COLOR_LABEL, width: 0.55, dash: null };
  }
}

function linkDistance(kind: KnowledgeLinkKind): number {
  switch (kind) {
    case "owns":        return 280;
    case "summarizes":  return 320;
    case "contains":    return 110;
    case "references":  return 60;
    case "located_at":  return 60;
    case "suggests":    return 90;
    case "matches":     return 220;
    default:            return 100;
  }
}

function linkStrength(kind: KnowledgeLinkKind): number {
  switch (kind) {
    case "matches":     return 0.04;
    case "summarizes":  return 0.4;
    case "owns":        return 0.4;
    default:            return 0.6;
  }
}

function buildNeighborMap(
  data: KnowledgeGraphData,
): Map<string, Set<string>> {
  const map = new Map<string, Set<string>>();
  const ensure = (id: string): Set<string> => {
    let s = map.get(id);
    if (!s) {
      s = new Set();
      map.set(id, s);
    }
    return s;
  };
  for (const n of data.nodes) ensure(n.id);
  for (const l of data.links) {
    const s = nodeIdOf(l.source);
    const t = nodeIdOf(l.target);
    if (!s || !t) continue;
    ensure(s).add(t);
    ensure(t).add(s);
  }
  return map;
}

// Pipeline ownership for findings + entities. Findings carry `pipeline` on
// the node. Entities don't, but each entity is attached to one or more
// findings — we resolve their owning pipeline via those finding links.
function buildPipelineOwnership(
  data: KnowledgeGraphData,
): Map<string, PipelineName> {
  const ownership = new Map<string, PipelineName>();
  for (const n of data.nodes) {
    if (n.kind === "finding" && n.pipeline) {
      ownership.set(n.id, n.pipeline);
    }
  }
  // Entities inherit the pipeline of any finding they're attached to.
  for (const link of data.links) {
    if (link.kind !== "references" && link.kind !== "located_at") continue;
    const s = nodeIdOf(link.source);
    const t = nodeIdOf(link.target);
    if (!s || !t) continue;
    const findingPipeline = ownership.get(s);
    if (findingPipeline && !ownership.has(t)) {
      ownership.set(t, findingPipeline);
    }
  }
  return ownership;
}

const KnowledgeGraphCanvas = forwardRef<KnowledgeGraphCanvasHandle, Props>(
  function KnowledgeGraphCanvas(props, ref) {
    const {
      data,
      width,
      height,
      selectedId,
      hoverId,
      onSelect,
      onHover,
    } = props;

    const fgRef = useRef<
      ForceGraphMethods<KnowledgeNode, KnowledgeLink> | undefined
    >(undefined);
    const positionsRef = useRef<Map<string, { x: number; y: number }>>(
      new Map(),
    );

    const neighbors = useMemo(() => buildNeighborMap(data), [data]);
    const ownership = useMemo(() => buildPipelineOwnership(data), [data]);

    const stableData = useMemo(() => {
      const cache = positionsRef.current;
      const nodes: ForcedNode[] = data.nodes.map((n) => {
        const anchor = ANCHOR_POSITIONS[n.id];
        if (anchor) {
          return { ...n, x: anchor.x, y: anchor.y, fx: anchor.x, fy: anchor.y };
        }
        const prior = cache.get(n.id);
        if (prior) return { ...n, x: prior.x, y: prior.y };
        const parent = parentNodeId(n.id, data.links);
        if (parent) {
          const parentPos = cache.get(parent) ?? ANCHOR_POSITIONS[parent];
          if (parentPos) {
            const jitter = 30;
            return {
              ...n,
              x: parentPos.x + (Math.random() - 0.5) * jitter,
              y: parentPos.y + 30 + (Math.random() - 0.5) * jitter,
            };
          }
        }
        return { ...n };
      });
      const links: ForcedLink[] = data.links.map((l) => ({ ...l }));
      return { nodes, links };
    }, [data]);

    useEffect(() => {
      const interval = window.setInterval(() => {
        for (const n of stableData.nodes) {
          if (typeof n.x === "number" && typeof n.y === "number") {
            positionsRef.current.set(n.id, { x: n.x, y: n.y });
          }
        }
      }, 250);
      return () => window.clearInterval(interval);
    }, [stableData]);

    // Tune d3-force after mount: stronger repulsion, kind-specific links,
    // plus a custom per-pipeline strip force that keeps findings/entities in
    // their owning pipeline's vertical column.
    useEffect(() => {
      const fg = fgRef.current;
      if (!fg) return;

      const charge = fg.d3Force("charge") as
        | { strength: (v: number) => unknown }
        | undefined;
      charge?.strength(-360);

      const link = fg.d3Force("link") as
        | {
            distance: (fn: (l: ForcedLink) => number) => unknown;
            strength: (fn: (l: ForcedLink) => number) => unknown;
          }
        | undefined;
      link?.distance((l) => linkDistance(l.kind));
      link?.strength((l) => linkStrength(l.kind));

      // Custom strip force — gentle pull toward each node's owning pipeline
      // strip. Anchor nodes are pinned so the force is a no-op for them.
      const stripForce = (alpha: number) => {
        for (const n of stableData.nodes) {
          if (ANCHOR_IDS.has(n.id)) continue;
          if (n.kind === "remediation") continue; // remediation orbits summary
          const pipeline = ownership.get(n.id);
          if (!pipeline) continue;
          if (typeof n.x !== "number" || typeof n.y !== "number") continue;
          const targetX = PIPELINE_STRIP_X[pipeline];
          const targetY = PIPELINE_STRIP_Y[pipeline];
          n.vx = (n.vx ?? 0) + (targetX - n.x) * 0.08 * alpha;
          n.vy = (n.vy ?? 0) + (targetY - n.y) * 0.04 * alpha;
        }
      };
      fg.d3Force("strip", stripForce as never);

      fg.d3ReheatSimulation();
    }, [stableData, ownership]);

    // Pin only the anchor nodes (profile / pipelines / summary).
    useEffect(() => {
      for (const n of stableData.nodes) {
        if (ANCHOR_IDS.has(n.id)) {
          const a = ANCHOR_POSITIONS[n.id];
          if (a) {
            n.fx = a.x;
            n.fy = a.y;
          }
          continue;
        }
        n.fx = undefined;
        n.fy = undefined;
      }
      fgRef.current?.d3ReheatSimulation();
    }, [stableData]);

    useImperativeHandle(
      ref,
      () => ({
        recenter: () => fgRef.current?.zoomToFit(400, 80),
        zoomIn: () => {
          const fg = fgRef.current;
          if (!fg) return;
          fg.zoom(fg.zoom() * 1.4, 250);
        },
        zoomOut: () => {
          const fg = fgRef.current;
          if (!fg) return;
          fg.zoom(fg.zoom() / 1.4, 250);
        },
      }),
      [],
    );

    useEffect(() => {
      const fg = fgRef.current;
      if (!fg) return;
      const t = window.setTimeout(() => fg.zoomToFit(700, 100), 800);
      return () => window.clearTimeout(t);
    }, [stableData]);

    const focusId = hoverId ?? selectedId;
    const focusSet = useMemo<Set<string> | null>(() => {
      if (!focusId) return null;
      const set = new Set<string>([focusId]);
      const ns = neighbors.get(focusId);
      if (ns) for (const n of ns) set.add(n);
      return set;
    }, [focusId, neighbors]);

    // Ghost connections — only when a node is *clicked* (not just hovered).
    // Surfaces 2-hop neighbors (friends-of-friends) as faint dotted spokes
    // from the selected node, turning the click into a hub-and-spoke view of
    // everything related, without cluttering the default layout.
    const ghostTargetIds = useMemo<Set<string>>(() => {
      if (!selectedId) return new Set();
      const oneHop = neighbors.get(selectedId) ?? new Set<string>();
      const out = new Set<string>();
      for (const a of oneHop) {
        const more = neighbors.get(a);
        if (!more) continue;
        for (const b of more) {
          if (b !== selectedId && !oneHop.has(b)) out.add(b);
        }
      }
      return out;
    }, [selectedId, neighbors]);

    const nodeById = useMemo<Map<string, ForcedNode>>(
      () => new Map(stableData.nodes.map((n) => [n.id, n])),
      [stableData],
    );

    // ---- Painters ----------------------------------------------------------

    const paintNode = (
      node: ForcedNode,
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ): void => {
      if (typeof node.x !== "number" || typeof node.y !== "number") return;
      const r = nodeRadius(node.kind);
      const fillBrightness = riskBrightness(node.risk);
      const isFocused = focusSet ? focusSet.has(node.id) : true;
      const isSelected = selectedId === node.id;
      const isHover = hoverId === node.id;
      const alpha = focusSet ? (isFocused ? 1 : 0.16) : 1;

      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.lineWidth = 1.2 / Math.max(0.6, Math.min(2, globalScale));

      switch (node.kind) {
        case "profile": {
          ctx.fillStyle = COLOR_TEXT;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = COLOR_TEXT;
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r + 4, 0, Math.PI * 2);
          ctx.stroke();
          break;
        }
        case "summary": {
          ctx.fillStyle = COLOR_TEXT;
          ctx.beginPath();
          ctx.moveTo(node.x, node.y - r);
          ctx.lineTo(node.x + r, node.y);
          ctx.lineTo(node.x, node.y + r);
          ctx.lineTo(node.x - r, node.y);
          ctx.closePath();
          ctx.fill();
          break;
        }
        case "pipeline": {
          ctx.strokeStyle = COLOR_TEXT;
          ctx.fillStyle = COLOR_BG;
          ctx.lineWidth = 1.4;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
          break;
        }
        case "finding": {
          ctx.fillStyle = fillBrightness;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fill();
          break;
        }
        case "remediation": {
          ctx.fillStyle = COLOR_BG;
          ctx.strokeStyle = COLOR_TEXT;
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.rect(node.x - r, node.y - r, r * 2, r * 2);
          ctx.fill();
          ctx.stroke();
          break;
        }
        default: {
          // location / platform / url / identity
          ctx.strokeStyle = COLOR_BORDER;
          ctx.fillStyle = fillBrightness === COLOR_TEXT ? COLOR_DIM : COLOR_LABEL;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        }
      }

      // Reactive feedback: bright outer ring on hover/select.
      if (isSelected || isHover) {
        ctx.strokeStyle = COLOR_TEXT;
        ctx.lineWidth = 1.8 / Math.max(0.8, Math.min(2, globalScale));
        ctx.globalAlpha = 1;
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + (isSelected ? 5 : 3.5), 0, Math.PI * 2);
        ctx.stroke();
      }

      const isAnchor = ANCHOR_IDS.has(node.id);
      const showLabel =
        isAnchor ||
        isSelected ||
        isHover ||
        (node.kind === "finding" && globalScale >= 1.2) ||
        (node.kind === "remediation" && globalScale >= 1.2) ||
        (!isAnchor && node.kind !== "finding" && node.kind !== "remediation"
          ? globalScale >= 1.7
          : false);

      if (showLabel) {
        const label = node.label;
        const fontPx = isAnchor
          ? Math.max(12, 13 / Math.max(0.7, Math.min(1.6, globalScale)))
          : Math.max(10, 11 / Math.max(0.7, Math.min(2.2, globalScale)));
        ctx.font = `${fontPx}px "JetBrains Mono", ui-monospace, monospace`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        const labelY = node.y + r + 4;
        const padX = 4;
        const metrics = ctx.measureText(label);
        const boxW = metrics.width + padX * 2;
        const boxH = fontPx + 4;
        ctx.fillStyle = COLOR_BG;
        ctx.fillRect(node.x - boxW / 2, labelY, boxW, boxH);
        ctx.fillStyle =
          isAnchor || isSelected || isHover ? COLOR_TEXT : COLOR_DIM;
        ctx.fillText(label, node.x, labelY + 2);
      }

      ctx.restore();
    };

    const paintLink = (
      link: ForcedLink,
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ): void => {
      const sourceNode =
        typeof link.source === "object" ? (link.source as ForcedNode) : null;
      const targetNode =
        typeof link.target === "object" ? (link.target as ForcedNode) : null;
      if (!sourceNode || !targetNode) return;
      if (
        typeof sourceNode.x !== "number" ||
        typeof sourceNode.y !== "number" ||
        typeof targetNode.x !== "number" ||
        typeof targetNode.y !== "number"
      ) {
        return;
      }
      const dim =
        focusSet !== null &&
        !(focusSet.has(sourceNode.id) && focusSet.has(targetNode.id));

      const isCross = link.kind === "matches";
      if (isCross && focusSet === null) {
        ctx.save();
        ctx.strokeStyle = COLOR_BORDER;
        ctx.globalAlpha = 0.4;
        ctx.lineWidth = 0.5;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(sourceNode.x, sourceNode.y);
        ctx.lineTo(targetNode.x, targetNode.y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
        return;
      }

      const style = linkStyle(link.kind);
      ctx.save();
      ctx.strokeStyle = dim ? COLOR_BORDER : style.color;
      ctx.lineWidth = style.width;
      ctx.globalAlpha = dim ? 0.22 : 0.85;
      if (style.dash) ctx.setLineDash(style.dash);
      ctx.beginPath();
      ctx.moveTo(sourceNode.x, sourceNode.y);
      ctx.lineTo(targetNode.x, targetNode.y);
      ctx.stroke();
      if (style.dash) ctx.setLineDash([]);
      ctx.restore();

      // When a node is clicked, label every spoke directly incident to it so
      // the user sees *what kind* of relationship each connection is. Only on
      // click — hover stays clean. Background box keeps the text legible
      // against any links/nodes underneath.
      const isSpokeOfClicked =
        selectedId !== null &&
        (sourceNode.id === selectedId || targetNode.id === selectedId);
      const SKIP_LABEL_KINDS: KnowledgeLinkKind[] = [
        "contains",
        "matches",
        "located_at",
      ];
      if (
        isSpokeOfClicked &&
        !dim &&
        !SKIP_LABEL_KINDS.includes(link.kind)
      ) {
        const label = linkKindLabel(link.kind);
        const midX = (sourceNode.x + targetNode.x) / 2;
        const midY = (sourceNode.y + targetNode.y) / 2;
        const dx = targetNode.x - sourceNode.x;
        const dy = targetNode.y - sourceNode.y;
        let angle = Math.atan2(dy, dx);
        // Flip so text always reads left-to-right rather than upside-down
        if (angle > Math.PI / 2 || angle < -Math.PI / 2) angle += Math.PI;

        const fontPx = Math.max(
          8,
          10 / Math.max(0.7, Math.min(2.2, globalScale)),
        );
        ctx.save();
        ctx.translate(midX, midY);
        ctx.rotate(angle);
        ctx.font = `${fontPx}px "JetBrains Mono", ui-monospace, monospace`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const padX = 4;
        const metrics = ctx.measureText(label);
        const boxW = metrics.width + padX * 2;
        const boxH = fontPx + 3;
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = COLOR_BG;
        ctx.fillRect(-boxW / 2, -boxH / 2, boxW, boxH);
        ctx.fillStyle = COLOR_TEXT;
        ctx.fillText(label, 0, 0);
        ctx.restore();
      }
    };

    const paintNodePointerArea = (
      node: ForcedNode,
      color: string,
      ctx: CanvasRenderingContext2D,
    ): void => {
      if (typeof node.x !== "number" || typeof node.y !== "number") return;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(node.x, node.y, nodeRadius(node.kind) + 5, 0, Math.PI * 2);
      ctx.fill();
    };

    // Draws faint dotted spokes from the selected node to every 2-hop
    // neighbor — only when something is *clicked* (not on hover). Runs
    // before the standard render so spokes sit behind the real graph.
    const drawGhostConnections = (
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ): void => {
      if (!selectedId || ghostTargetIds.size === 0) return;
      const sel = nodeById.get(selectedId);
      if (!sel || typeof sel.x !== "number" || typeof sel.y !== "number") {
        return;
      }
      ctx.save();
      ctx.strokeStyle = COLOR_DIM;
      ctx.globalAlpha = 0.30;
      ctx.lineWidth = 0.4 / Math.max(0.6, Math.min(2, globalScale));
      ctx.setLineDash([3, 4]);
      for (const id of ghostTargetIds) {
        const t = nodeById.get(id);
        if (!t || typeof t.x !== "number" || typeof t.y !== "number") continue;
        ctx.beginPath();
        ctx.moveTo(sel.x, sel.y);
        ctx.lineTo(t.x, t.y);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.restore();
    };

    return (
      <ForceGraph2D<KnowledgeNode, KnowledgeLink>
        ref={fgRef}
        graphData={stableData}
        width={Math.max(320, width)}
        height={Math.max(320, height)}
        backgroundColor={COLOR_BG}
        nodeRelSize={5}
        nodeVal={(n: ForcedNode) => nodeVal(n.kind)}
        nodeLabel={(n: ForcedNode) => n.label}
        nodeCanvasObject={paintNode}
        nodeCanvasObjectMode={() => "replace"}
        nodePointerAreaPaint={paintNodePointerArea}
        linkCanvasObject={paintLink}
        linkCanvasObjectMode={() => "replace"}
        onRenderFramePre={drawGhostConnections}
        linkColor={() => COLOR_DIM}
        linkWidth={(l: ForcedLink) => linkStyle(l.kind).width}
        cooldownTicks={180}
        cooldownTime={6000}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.34}
        warmupTicks={60}
        onNodeClick={(n: ForcedNode) => onSelect(n.id)}
        onNodeHover={(n: ForcedNode | null) => onHover(n?.id ?? null)}
        onBackgroundClick={() => onSelect(null)}
        enableNodeDrag
        enableZoomInteraction
        enablePanInteraction
        minZoom={0.3}
        maxZoom={6}
      />
    );
  },
);

export default KnowledgeGraphCanvas;
