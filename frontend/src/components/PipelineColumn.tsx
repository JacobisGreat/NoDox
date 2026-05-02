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
          <EmptyState status={status} />
        ) : (
          findings.map((finding, idx) => (
            <FindingCard key={`${pipeline}-${idx}`} finding={finding} />
          ))
        )}
      </div>
    </section>
  );
}

function EmptyState({ status }: { status: PipelineStatusState }) {
  let label = "awaiting findings...";
  if (status.status === "running") {
    label = (status.detail ?? "pipeline running...").toLowerCase();
  } else if (status.status === "complete") {
    label = "no findings surfaced.";
  } else if (status.status === "error") {
    label = (status.detail ?? "pipeline error.").toLowerCase();
  } else if (status.status === "budget_exceeded") {
    label = (status.detail ?? "budget exceeded.").toLowerCase();
  }
  return (
    <div className="flex h-full min-h-[180px] items-center justify-center border border-dashed border-nodoxx-border px-4 py-8 text-center font-mono text-[11px] text-nodoxx-muted">
      &gt; {label}
    </div>
  );
}
