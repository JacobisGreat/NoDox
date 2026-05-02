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

      <main className="mx-auto w-full max-w-[1280px] px-4 py-6 sm:px-6">
        {(audit.startError || audit.streamError) && (
          <div className="mb-6 border border-kali-border bg-kali-surface px-4 py-3 font-mono text-[13px] text-kali-text">
            <span className="text-kali-label">[err] </span>
            {audit.startError ?? audit.streamError}
          </div>
        )}

        {audit.aggregator ? (
          <ExposureScore aggregator={audit.aggregator} />
        ) : (
          <section
            className="border border-kali-border bg-kali-surface p-6"
            aria-live="polite"
          >
            <div className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className="font-mono text-[13px] text-kali-text animate-running-pulse"
              >
                [...]
              </span>
              <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
                // working through your audit
              </span>
            </div>
            <ul className="mt-4 space-y-2 font-mono text-[13px] text-kali-text">
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
                      className={`select-none ${
                        done ? "text-kali-text" : "text-kali-dim"
                      }`}
                    >
                      {done ? "[x]" : s.status === "running" ? "[~]" : "[ ]"}
                    </span>
                    <span className="font-bold uppercase tracking-label text-kali-text">
                      {pipelineTitle(p)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-kali-dim">
                      {progressLine(p, s)}
                    </span>
                  </li>
                );
              })}
            </ul>
            <p className="mt-4 font-mono text-[11px] uppercase tracking-label text-kali-label">
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
