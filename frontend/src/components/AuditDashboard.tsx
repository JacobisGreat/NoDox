import { PipelineName } from "../types";
import { useAudit } from "../hooks/useAudit";
import AuditHeader from "./AuditHeader";
import PipelineColumn from "./PipelineColumn";
import RemediationDrawer from "./RemediationDrawer";
import ExposureScore from "./ExposureScore";
import GeoMap from "./GeoMap";

interface Props {
  sessionId: string;
}

const PIPELINES: PipelineName[] = ["identity", "geolocation", "web_footprint"];

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
          <section className="border border-nodoxx-border bg-nodoxx-panel p-6">
            <div className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-nodoxx-dim border-t-nodoxx-text"
              />
              <span className="font-mono text-xs uppercase tracking-[0.18em] text-nodoxx-muted">
                pipelines running. findings stream below.
              </span>
            </div>
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
