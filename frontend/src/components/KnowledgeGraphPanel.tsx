import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AggregatorResult,
  Finding,
  KnowledgeGraphData,
  KnowledgeLink,
  KnowledgeNode,
  PipelineName,
  Profile,
  RemediationItem,
} from "../types";
import {
  buildKnowledgeGraph,
  linkKindLabel,
  nodeIdOf,
  nodeKindLabel,
} from "../utils/knowledgeGraph";
import { riskBgClass } from "../utils/format";
import KnowledgeGraphCanvas, {
  KnowledgeGraphCanvasHandle,
} from "./KnowledgeGraphCanvas";

interface Props {
  profile: Profile | null;
  findingsByPipeline: Record<PipelineName, Finding[]>;
  aggregator: AggregatorResult | null;
}

export default function KnowledgeGraphPanel({
  profile,
  findingsByPipeline,
  aggregator,
}: Props) {
  const totalFindings =
    findingsByPipeline.identity.length +
    findingsByPipeline.geolocation.length +
    findingsByPipeline.web_footprint.length;

  const fullGraph = useMemo<KnowledgeGraphData>(
    () => buildKnowledgeGraph({ profile, findingsByPipeline, aggregator }),
    [profile, findingsByPipeline, aggregator],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<KnowledgeGraphCanvasHandle | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({
    w: 600,
    h: 620,
  });

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      const desired = window.innerWidth < 640 ? 440 : 620;
      setSize({ w: Math.max(320, Math.floor(rect.width)), h: desired });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    if (!fullGraph.nodes.some((n) => n.id === selectedId)) setSelectedId(null);
  }, [fullGraph, selectedId]);

  const selectedNode =
    selectedId === null
      ? null
      : (fullGraph.nodes.find((n) => n.id === selectedId) ?? null);

  const incidentLinks = useMemo<KnowledgeLink[]>(() => {
    if (!selectedId) return [];
    return fullGraph.links.filter((l) => {
      const s = nodeIdOf(l.source);
      const t = nodeIdOf(l.target);
      return s === selectedId || t === selectedId;
    });
  }, [fullGraph, selectedId]);

  if (totalFindings === 0 && !aggregator) return null;

  const handleSelect = (id: string | null) => {
    setSelectedId((prev) => {
      if (id === null) return null;
      return prev === id ? null : id;
    });
  };

  return (
    <section className="border border-kali-border bg-kali-surface transition-colors hover:border-kali-text">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-kali-border px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h2 className="font-mono text-[13px] font-bold uppercase tracking-label text-kali-text">
            // knowledge web
          </h2>
          <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
            {fullGraph.nodes.length} nodes
          </span>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-0 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div
          ref={containerRef}
          className="relative bg-kali-bg lg:border-r lg:border-kali-border"
          style={{ minHeight: 460 }}
        >
          {fullGraph.nodes.length === 0 ? (
            <div className="flex h-[460px] items-center justify-center font-mono text-[11px] text-kali-dim">
              no nodes yet
            </div>
          ) : (
            <KnowledgeGraphCanvas
              ref={canvasRef}
              data={fullGraph}
              width={size.w}
              height={size.h}
              selectedId={selectedId}
              hoverId={hoverId}
              onSelect={handleSelect}
              onHover={setHoverId}
            />
          )}

          <div className="pointer-events-auto absolute right-3 top-3 flex items-stretch gap-px overflow-hidden border border-kali-border bg-kali-bg">
            <ToolbarButton
              onClick={() => canvasRef.current?.zoomIn()}
              ariaLabel="Zoom in"
            >
              +
            </ToolbarButton>
            <ToolbarButton
              onClick={() => canvasRef.current?.zoomOut()}
              ariaLabel="Zoom out"
            >
              &minus;
            </ToolbarButton>
            <ToolbarButton onClick={() => canvasRef.current?.recenter()}>
              fit
            </ToolbarButton>
          </div>
        </div>

        <DetailPanel
          node={selectedNode}
          links={incidentLinks}
          allNodes={fullGraph.nodes}
        />
      </div>
    </section>
  );
}

interface ToolbarButtonProps {
  onClick: () => void;
  active?: boolean;
  ariaLabel?: string;
  children: React.ReactNode;
}

function ToolbarButton({
  onClick,
  active,
  ariaLabel,
  children,
}: ToolbarButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      className={`px-2.5 py-1 font-mono text-[11px] tabular-nums uppercase tracking-label transition-colors ${
        active
          ? "bg-kali-text text-kali-bg"
          : "bg-kali-bg text-kali-dim hover:bg-kali-surface hover:text-kali-text"
      }`}
    >
      {children}
    </button>
  );
}

interface DetailProps {
  node: KnowledgeNode | null;
  links: KnowledgeLink[];
  allNodes: KnowledgeNode[];
}

function DetailPanel({ node, links, allNodes }: DetailProps) {
  if (!node) {
    return (
      <aside className="flex min-h-[200px] flex-col gap-2 border-t border-kali-border bg-kali-surface px-5 py-5 lg:border-t-0">
        <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
          node detail
        </span>
        <p className="font-mono text-[12px] leading-relaxed text-kali-dim">
          Click a node to see its evidence and connections. Hover to highlight
          immediate neighbors.
        </p>
      </aside>
    );
  }

  const incident = links.map((l) => {
    const sId = nodeIdOf(l.source) ?? "";
    const tId = nodeIdOf(l.target) ?? "";
    const otherId = sId === node.id ? tId : sId;
    const other = allNodes.find((n) => n.id === otherId);
    return { link: l, other };
  });

  return (
    <aside className="flex flex-col gap-4 border-t border-kali-border bg-kali-surface px-5 py-5 font-mono text-[11px] text-kali-text lg:border-t-0">
      <header className="flex items-start justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
          {nodeKindLabel(node.kind)}
        </span>
        {node.risk && (
          <span
            className={`shrink-0 border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-label ${riskBgClass(node.risk)}`}
          >
            {node.risk}
          </span>
        )}
      </header>

      <h3 className="break-words font-mono text-[15px] font-bold text-kali-text">
        {node.label}
      </h3>

      {node.detail && (
        <p className="prose text-[13px] text-kali-text">{node.detail}</p>
      )}

      {node.kind === "remediation" && node.metadata && (
        <RemediationDetail item={node.metadata as unknown as RemediationItem} />
      )}

      {node.kind === "finding" && node.metadata && (
        <FindingDetail
          finding={(node.metadata as { _finding?: Finding })._finding ?? null}
        />
      )}

      {incident.length > 0 && (
        <div className="space-y-1.5">
          <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
            connections &middot; {incident.length}
          </span>
          <ul className="max-h-56 space-y-1 overflow-y-auto pr-1">
            {incident.slice(0, 24).map((entry, idx) => (
              <li
                key={idx}
                className="flex items-start gap-2 border border-kali-border px-2 py-1"
              >
                <span className="shrink-0 font-mono text-[10px] uppercase tracking-label text-kali-label">
                  {linkKindLabel(entry.link.kind)}
                </span>
                <span className="min-w-0 truncate font-mono text-[11px] text-kali-text">
                  {entry.other?.label ?? "?"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}

function RemediationDetail({ item }: { item: RemediationItem }) {
  if (!item) return null;
  return (
    <div className="border-l border-kali-text bg-kali-bg px-3 py-2">
      <span className="mb-1 block font-mono text-[10px] uppercase tracking-label text-kali-label">
        priority {item.priority}
      </span>
      <p className="prose text-[13px] text-kali-text">{item.action}</p>
      {item.reason && (
        <p className="mt-2 font-mono text-[11px] text-kali-dim">
          {item.reason}
        </p>
      )}
    </div>
  );
}

function FindingDetail({ finding }: { finding: Finding | null }) {
  if (!finding) return null;
  const evidence = finding.evidence_chain.slice(0, 4);
  return (
    <div className="space-y-2">
      <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
        evidence
      </span>
      <ul className="space-y-1">
        {evidence.map((e, idx) => (
          <li
            key={idx}
            className="break-words font-mono text-[12px] leading-relaxed text-kali-text"
          >
            {e}
          </li>
        ))}
      </ul>
      {finding.remediation && (
        <div className="border-l border-kali-text bg-kali-bg px-3 py-2">
          <span className="mb-1 block font-mono text-[10px] uppercase tracking-label text-kali-label">
            remediation
          </span>
          <p className="prose text-[13px] text-kali-text">
            {finding.remediation}
          </p>
        </div>
      )}
    </div>
  );
}
