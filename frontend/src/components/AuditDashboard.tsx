import { PipelineName } from "../types";
import { PipelineStatusState, useAudit } from "../hooks/useAudit";
import { pipelineTitle } from "../utils/format";
import AuditHeader from "./AuditHeader";
import PipelineColumn from "./PipelineColumn";
import RemediationDrawer from "./RemediationDrawer";
import ExposureScore from "./ExposureScore";
import GeoMap from "./GeoMap";

interface Props {
  sessionId: string;
}

const PIPELINES: PipelineName[] = ["identity", "geolocation", "web_footprint"];

// Defaults mirror the per-column copy. The hero loading panel uses these so
// the user has something concrete to read while the score is computing —
// "what's happening" beats a generic spinner for both perceived speed and
// trust (Maister, 1985).
const PIPELINE_PROGRESS: Record<PipelineName, string> = {
  identity: "sweeping 1,001 platforms for username matches",
  geolocation: "reading EXIF, captions, and visual cues from posts",
  web_footprint: "searching the open web for this handle",
};

function progressLine(p: PipelineName, s: PipelineStatusState): string {
  if (s.status === "complete") return "complete.";
  if (s.status === "error") return s.detail ?? "error.";
  if (s.status === "budget_exceeded") return s.detail ?? "budget exceeded.";
  if (s.status === "idle") return "queued.";
  return (s.detail ?? PIPELINE_PROGRESS[p]).toLowerCase();
}

export default function AuditDashboard({ sessionId }: Props) {
  const audit = useAudit(sessionId);

  return (
    <div className="min-h-screen pb-24">
      <AuditHeader
        profile={audit.profile}
        totalFindings={audit.totalFindings}
        cost={audit.cost}
        costTick={audit.costTick}
        pipelineStatuses={audit.pipelineStatuses}
        aggregator={audit.aggregator}
        connected={audit.connected}
        streamDone={audit.streamDone}
      />

      <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6">
        {(audit.startError || audit.streamError) && (
          <div className="mb-6 border border-nodoxx-text/40 bg-white/[0.04] px-4 py-3 font-mono text-sm text-nodoxx-text">
            <span className="text-nodoxx-muted">err: </span>
            {audit.startError ?? audit.streamError}
          </div>
        )}

        {audit.aggregator ? (
          <ExposureScore aggregator={audit.aggregator} />
        ) : (
          <section
            className="border border-nodoxx-border bg-nodoxx-panel p-6"
            aria-live="polite"
          >
            <div className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-nodoxx-dim border-t-nodoxx-text"
              />
              <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-nodoxx-muted">
                // working through your audit
              </span>
            </div>
            <ul className="mt-4 space-y-2 font-mono text-[13px] text-nodoxx-text/90">
              {PIPELINES.map((p) => {
                const s = audit.pipelineStatuses[p];
                const done =
                  s.status === "complete" ||
                  s.status === "error" ||
                  s.status === "budget_exceeded";
                return (
                  <li key={p} className="flex items-baseline gap-3">
                    <span
                      aria-hidden="true"
                      className={`select-none text-xs ${
                        done ? "text-nodoxx-text" : "text-nodoxx-muted"
                      }`}
                    >
                      {done ? "[x]" : s.status === "running" ? "[~]" : "[ ]"}
                    </span>
                    <span className="font-semibold uppercase tracking-[0.14em] text-nodoxx-text">
                      {pipelineTitle(p)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-nodoxx-muted">
                      {progressLine(p, s)}
                    </span>
                  </li>
                );
              })}
            </ul>
            <p className="mt-4 font-mono text-[11px] uppercase tracking-[0.18em] text-nodoxx-muted">
              &gt; exposure score &amp; remediation plan land here when all three finish.
            </p>
          </section>
        )}

        <section className="mt-6">
          <GeoMap findings={audit.findingsByPipeline.geolocation} />
        </section>

        <section className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
          {PIPELINES.map((p) => (
            <PipelineColumn
              key={p}
              pipeline={p}
              status={audit.pipelineStatuses[p]}
              findings={audit.findingsByPipeline[p]}
            />
          ))}
        </section>
      </main>

      <RemediationDrawer aggregator={audit.aggregator} />
    </div>
  );
}
