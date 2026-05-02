import { Finding, PipelineName } from "../types";
import { pipelineTitle } from "../utils/format";
import { PipelineStatusState } from "../hooks/useAudit";
import FindingCard from "./FindingCard";
import StatusChip from "./StatusChip";

interface Props {
  pipeline: PipelineName;
  status: PipelineStatusState;
  findings: Finding[];
}

// Per-pipeline microcopy. Generic "pipeline running..." wastes a slot
// where we could be communicating *what* the audit is actually doing.
// Concrete scope reduces uncertainty and shortens perceived wait time
// (Maister: "filled time feels shorter than empty time"). Sub-second
// visibility into the work also reinforces the audit's credibility — the
// user can see we're touching real data, not just spinning a circle.
const PIPELINE_COPY: Record<
  PipelineName,
  { running: string; clean: string; awaiting: string }
> = {
  identity: {
    running: "sweeping 1,001 platforms for matching usernames...",
    clean: "no identity matches across 1,001 platforms.",
    awaiting: "queued. identity sweep starts shortly.",
  },
  geolocation: {
    running: "reading EXIF, captions, and visual cues from posts...",
    clean: "no location signals surfaced from public posts.",
    awaiting: "queued. geolocation analysis starts shortly.",
  },
  web_footprint: {
    running: "searching the open web for this handle...",
    clean: "no public web mentions matched.",
    awaiting: "queued. web footprint search starts shortly.",
  },
};

export default function PipelineColumn({ pipeline, status, findings }: Props) {
  return (
    <section className="flex min-h-[320px] flex-col border border-nodoxx-border bg-nodoxx-panel">
      <header className="flex items-center justify-between gap-3 border-b border-nodoxx-border px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-nodoxx-text">
            // {pipelineTitle(pipeline)}
          </h2>
          <span className="border border-nodoxx-border bg-nodoxx-bg px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-nodoxx-muted">
            {findings.length}
          </span>
        </div>
        <StatusChip status={status.status} detail={status.detail} />
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-4 lg:max-h-[calc(100vh-320px)]">
        {findings.length === 0 ? (
          <EmptyState pipeline={pipeline} status={status} />
        ) : (
          findings.map((finding, idx) => (
            <FindingCard key={`${pipeline}-${idx}`} finding={finding} />
          ))
        )}
      </div>
    </section>
  );
}

function EmptyState({
  pipeline,
  status,
}: {
  pipeline: PipelineName;
  status: PipelineStatusState;
}) {
  const copy = PIPELINE_COPY[pipeline];
  let label = copy.awaiting;
  if (status.status === "running") {
    label = (status.detail ?? copy.running).toLowerCase();
  } else if (status.status === "complete") {
    label = copy.clean;
  } else if (status.status === "error") {
    label = (status.detail ?? "pipeline error.").toLowerCase();
  } else if (status.status === "budget_exceeded") {
    label = (status.detail ?? "budget exceeded.").toLowerCase();
  }
  return (
    <div className="flex h-full min-h-[180px] items-center justify-center border border-dashed border-nodoxx-border px-4 py-8 text-center font-mono text-[11px] leading-relaxed text-nodoxx-muted">
      &gt; {label}
    </div>
  );
}
