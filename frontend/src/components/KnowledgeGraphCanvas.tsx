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
  RiskLevel,
} from "../types";
import {
  ANCHOR_IDS,
  PROFILE_NODE_ID,
  SUMMARY_NODE_ID,
  pipelineNodeId,
} from "../utils/knowledgeGraph";

export interface KnowledgeGraphCanvasHandle {
  recenter: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  releasePins: () => void;
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
  freeze: boolean;
}

const COLOR_TEXT = "#FFFFFF";
const COLOR_DIM = "#A1A1A1";
const COLOR_LABEL = "#666666";
const COLOR_BORDER = "#1F1F1F";
const COLOR_BG = "#000000";

// Fixed coordinates that pin the five anchor nodes into a stable spatial
// frame. Profile sits center-top, three pipelines fan around it, summary
// anchors the bottom. Everything else is force-laid-out around these.
const ANCHOR_POSITIONS: Record<string, { x: number; y: number }> = {
  [PROFILE_NODE_ID]: { x: 0, y: -260 },
  [pipelineNodeId("identity")]: { x: -260, y: -60 },
  [pipelineNodeId("geolocation")]: { x: 0, y: -60 },
  [pipelineNodeId("web_footprint")]: { x: 260, y: -60 },
  [SUMMARY_NODE_ID]: { x: 0, y: 240 },
};

function riskBrightness(risk?: RiskLevel): string {
  if (risk === "CRITICAL" || risk === "HIGH") return COLOR_TEXT;
  if (risk === "MEDIUM") return COLOR_DIM;
  if (risk === "LOW") return COLOR_LABEL;
  return COLOR_LABEL;
}

function nodeRadius(kind: KnowledgeNodeKind): number {
  switch (kind) {
    case "profile":     return 8;
    case "summary":     return 7;
    case "pipeline":    return 6;
    case "finding":     return 3.4;
    case "remediation": return 3.2;
    case "location":    return 2.6;
    case "platform":    return 2.4;
    case "url":         return 2.4;
    case "identity":    return 2.6;
    default:            return 2.4;
  }
}

function nodeVal(kind: KnowledgeNodeKind): number {
  return Math.max(2, nodeRadius(kind) * 0.9);
}

// Three link styles only:
//   structural — solid white-ish, builds the skeleton
//   reference  — solid label-gray, very thin, finding↔entity
//   cross      — dashed white, low-weight remediation↔finding shortcuts
function linkStyle(kind: KnowledgeLinkKind): {
  color: string;
  width: number;
  dash: number[] | null;
} {
  switch (kind) {
    case "owns":
      return { color: COLOR_TEXT, width: 1, dash: null };
    case "summarizes":
      return { color: COLOR_DIM, width: 0.9, dash: null };
    case "contains":
      return { color: COLOR_DIM, width: 0.7, dash: null };
    case "suggests":
      return { color: COLOR_DIM, width: 0.7, dash: null };
    case "located_at":
    case "references":
      return { color: COLOR_LABEL, width: 0.5, dash: null };
    case "matches":
      return { color: COLOR_DIM, width: 0.5, dash: [3, 3] };
    default:
      return { color: COLOR_LABEL, width: 0.5, dash: null };
  }
}

// Default link distance + strength per kind. Anchors are pinned, so anchor↔
// anchor distances only affect cosmetics — the forces matter for the cloud
// of unpinned findings, entities, and remediation items.
function linkDistance(kind: KnowledgeLinkKind): number {
  switch (kind) {
    case "owns":        return 200;
    case "summarizes":  return 220;
    case "contains":    return 70;
    case "references":  return 42;
    case "located_at":  return 42;
    case "suggests":    return 60;
    case "matches":     return 160;
    default:            return 80;
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

function nodeIdOf(
  ref: string | number | { id?: string | number } | undefined,
): string | null {
  if (ref == null) return null;
  if (typeof ref === "string") return ref;
  if (typeof ref === "number") return String(ref);
  if (typeof ref.id === "string") return ref.id;
  if (typeof ref.id === "number") return String(ref.id);
  return null;
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
      freeze,
    } = props;

    const fgRef = useRef<
      ForceGraphMethods<KnowledgeNode, KnowledgeLink> | undefined
    >(undefined);
    const positionsRef = useRef<Map<string, { x: number; y: number }>>(
      new Map(),
    );

    const neighbors = useMemo(() => buildNeighborMap(data), [data]);

    // Carry x/y from the previous data so the layout doesn't pop on rebuild.
    const stableData = useMemo(() => {
      const cache = positionsRef.current;
      const nodes: ForcedNode[] = data.nodes.map((n) => {
        const anchor = ANCHOR_POSITIONS[n.id];
        if (anchor) {
          return { ...n, x: anchor.x, y: anchor.y, fx: anchor.x, fy: anchor.y };
        }
        const prior = cache.get(n.id);
        return prior ? { ...n, x: prior.x, y: prior.y } : { ...n };
      });
      const links: ForcedLink[] = data.links.map((l) => ({ ...l }));
      return { nodes, links };
    }, [data]);

    // Persist position cache so subsequent rebuilds can rehydrate non-anchors.
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

    // Tune the d3-force engine after mount so the unpinned cloud spreads
    // cleanly around the pinned skeleton.
    useEffect(() => {
      const fg = fgRef.current;
      if (!fg) return;
      const charge = fg.d3Force("charge") as
        | { strength: (v: number) => unknown }
        | undefined;
      charge?.strength(-220);
      const link = fg.d3Force("link") as
        | {
            distance: (fn: (l: ForcedLink) => number) => unknown;
            strength: (fn: (l: ForcedLink) => number) => unknown;
          }
        | undefined;
      link?.distance((l) => linkDistance(l.kind));
      link?.strength((l) => linkStrength(l.kind));
      fg.d3ReheatSimulation();
    }, [stableData]);

    // Re-pin anchors and apply / release freeze for non-anchors.
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
        if (freeze) {
          if (typeof n.x === "number") n.fx = n.x;
          if (typeof n.y === "number") n.fy = n.y;
        } else {
          n.fx = undefined;
          n.fy = undefined;
        }
      }
      const fg = fgRef.current;
      if (fg && !freeze) fg.d3ReheatSimulation();
    }, [freeze, stableData]);

    useImperativeHandle(
      ref,
      () => ({
        recenter: () => {
          fgRef.current?.zoomToFit(400, 60);
        },
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
        releasePins: () => {
          for (const n of stableData.nodes) {
            if (ANCHOR_IDS.has(n.id)) continue;
            n.fx = undefined;
            n.fy = undefined;
          }
          fgRef.current?.d3ReheatSimulation();
        },
      }),
      [stableData],
    );

    // Auto-fit once on first stable layout.
    useEffect(() => {
      const fg = fgRef.current;
      if (!fg) return;
      const t = window.setTimeout(() => fg.zoomToFit(700, 80), 750);
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
      ctx.lineWidth = 1 / Math.max(0.6, Math.min(2, globalScale));

      switch (node.kind) {
        case "profile": {
          ctx.fillStyle = COLOR_TEXT;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = COLOR_TEXT;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r + 2.5, 0, Math.PI * 2);
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
          ctx.beginPath();
          ctx.rect(node.x - r, node.y - r, r * 2, r * 2);
          ctx.fill();
          ctx.stroke();
          break;
        }
        default: {
          // location / platform / url / identity — small hollow circles
          ctx.strokeStyle = COLOR_BORDER;
          ctx.fillStyle = fillBrightness === COLOR_TEXT ? COLOR_DIM : COLOR_LABEL;
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        }
      }

      if (isSelected || isHover) {
        ctx.strokeStyle = COLOR_TEXT;
        ctx.lineWidth = 1.5 / Math.max(0.8, Math.min(2, globalScale));
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + (isSelected ? 4 : 2.5), 0, Math.PI * 2);
        ctx.stroke();
      }

      // Label policy:
      //   anchors  → always
      //   self-hover/select → always
      //   findings → at scale ≥ 1.4
      //   entities → at scale ≥ 2.0
      const isAnchor = ANCHOR_IDS.has(node.id);
      const showLabel =
        isAnchor ||
        isSelected ||
        isHover ||
        (node.kind === "finding" && globalScale >= 1.4) ||
        (node.kind === "remediation" && globalScale >= 1.4) ||
        (!isAnchor && node.kind !== "finding" && node.kind !== "remediation"
          ? globalScale >= 2.0
          : false);

      if (showLabel) {
        const label = node.label;
        const fontPx = isAnchor
          ? Math.max(10, 11 / Math.max(0.7, Math.min(1.6, globalScale)))
          : Math.max(8, 9 / Math.max(0.7, Math.min(2.2, globalScale)));
        ctx.font = `${fontPx}px "JetBrains Mono", ui-monospace, monospace`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        const labelY = node.y + r + 3;
        const padX = 3;
        const metrics = ctx.measureText(label);
        const boxW = metrics.width + padX * 2;
        const boxH = fontPx + 3;
        ctx.fillStyle = COLOR_BG;
        ctx.fillRect(node.x - boxW / 2, labelY, boxW, boxH);
        ctx.fillStyle = isAnchor || isSelected || isHover ? COLOR_TEXT : COLOR_DIM;
        ctx.fillText(label, node.x, labelY + 1);
      }

      ctx.restore();
    };

    const paintLink = (
      link: ForcedLink,
      ctx: CanvasRenderingContext2D,
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

      // Cross-links are barely visible by default; surface them on focus.
      const isCross = link.kind === "matches";
      if (isCross && focusSet === null) {
        ctx.save();
        ctx.strokeStyle = COLOR_BORDER;
        ctx.globalAlpha = 0.35;
        ctx.lineWidth = 0.4;
        ctx.setLineDash([3, 3]);
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
    };

    const paintNodePointerArea = (
      node: ForcedNode,
      color: string,
      ctx: CanvasRenderingContext2D,
    ): void => {
      if (typeof node.x !== "number" || typeof node.y !== "number") return;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(node.x, node.y, nodeRadius(node.kind) + 4, 0, Math.PI * 2);
      ctx.fill();
    };

    return (
      <ForceGraph2D<KnowledgeNode, KnowledgeLink>
        ref={fgRef}
        graphData={stableData}
        width={Math.max(320, width)}
        height={Math.max(280, height)}
        backgroundColor={COLOR_BG}
        nodeRelSize={4}
        nodeVal={(n: ForcedNode) => nodeVal(n.kind)}
        nodeLabel={(n: ForcedNode) => n.label}
        nodeCanvasObject={paintNode}
        nodeCanvasObjectMode={() => "replace"}
        nodePointerAreaPaint={paintNodePointerArea}
        linkCanvasObject={paintLink}
        linkCanvasObjectMode={() => "replace"}
        linkColor={() => COLOR_DIM}
        linkWidth={(l: ForcedLink) => linkStyle(l.kind).width}
        cooldownTicks={140}
        cooldownTime={5000}
        d3AlphaDecay={0.022}
        d3VelocityDecay={0.36}
        warmupTicks={40}
        onNodeClick={(n: ForcedNode) =>
          onSelect(selectedId === n.id ? null : n.id)
        }
        onNodeHover={(n: ForcedNode | null) => onHover(n?.id ?? null)}
        onBackgroundClick={() => onSelect(null)}
        enableNodeDrag
        enableZoomInteraction
        enablePanInteraction
        minZoom={0.4}
        maxZoom={6}
      />
    );
  },
);

export default KnowledgeGraphCanvas;
