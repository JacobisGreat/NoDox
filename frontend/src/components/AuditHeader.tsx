import { AggregatorResult, PipelineName, Profile } from "../types";
import { PipelineStatusState } from "../hooks/useAudit";
import { formatCount, pipelineTitle } from "../utils/format";
import StatusChip from "./StatusChip";
import CostDisplay from "./CostDisplay";
import ExposureScore from "./ExposureScore";

interface Props {
  profile: Profile | null;
  totalFindings: number;
  cost: number;
  costTick: number;
  pipelineStatuses: Record<PipelineName, PipelineStatusState>;
  aggregator: AggregatorResult | null;
  connected: boolean;
  streamDone: boolean;
}

const PIPELINES: PipelineName[] = ["identity", "geolocation", "web_footprint"];

export default function AuditHeader({
  profile,
  totalFindings,
  cost,
  costTick,
  pipelineStatuses,
  aggregator,
  connected,
  streamDone,
}: Props) {
  return (
    <header className="sticky top-0 z-30 border-b border-nodoxx-border/30 bg-nodoxx-bg/95 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-3 px-4 py-3 sm:px-6 lg:flex-row lg:items-center lg:gap-6">
        <div className="flex min-w-0 items-center gap-3">
          <a
            href="/"
            className="select-none font-mono text-base font-bold tracking-[0.18em] text-nodoxx-accent hover:brightness-110"
          >
            NODOXX
          </a>
          <span className="hidden h-5 w-px bg-nodoxx-border/40 sm:block" />
          {profile ? (
            <ProfileBlock profile={profile} />
          ) : (
            <ProfileSkeleton />
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 lg:ml-auto">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-xl font-bold tabular-nums text-nodoxx-text">
              {totalFindings}
            </span>
            <span className="text-xs uppercase tracking-wider text-nodoxx-muted">
              findings
            </span>
          </div>

          <ExposureScore aggregator={aggregator} compact />

          <CostDisplay cost={cost} tick={costTick} />

          <ConnectionDot connected={connected} streamDone={streamDone} />
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-2 px-4 pb-3 sm:px-6">
        {PIPELINES.map((p) => (
          <div key={p} className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wider text-nodoxx-muted">
              {pipelineTitle(p)}
            </span>
            <StatusChip
              status={pipelineStatuses[p].status}
              detail={pipelineStatuses[p].detail}
            />
          </div>
        ))}
      </div>
    </header>
  );
}

function ProfileBlock({ profile }: { profile: Profile }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <img
        src={profile.profile_pic_url}
        alt=""
        referrerPolicy="no-referrer"
        className="h-10 w-10 flex-shrink-0 rounded-full border border-nodoxx-border/40 bg-nodoxx-panel object-cover"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
        }}
      />
      <div className="flex min-w-0 flex-col leading-tight">
        <span className="truncate font-mono text-sm text-nodoxx-text">
          @{profile.username}
        </span>
        <span className="font-mono text-[11px] text-nodoxx-muted tabular-nums">
          {formatCount(profile.followers)} followers
        </span>
      </div>
    </div>
  );
}

function ProfileSkeleton() {
  return (
    <div className="flex items-center gap-3">
      <div className="h-10 w-10 animate-pulse rounded-full bg-nodoxx-panel" />
      <div className="flex flex-col gap-1">
        <div className="h-3 w-24 animate-pulse rounded bg-nodoxx-panel" />
        <div className="h-2 w-16 animate-pulse rounded bg-nodoxx-panel" />
      </div>
    </div>
  );
}

function ConnectionDot({
  connected,
  streamDone,
}: {
  connected: boolean;
  streamDone: boolean;
}) {
  let color = "bg-slate-500";
  let label = "connecting";
  if (streamDone) {
    color = "bg-risk-low";
    label = "done";
  } else if (connected) {
    color = "bg-nodoxx-accent animate-running-pulse";
    label = "live";
  }
  return (
    <span
      title={label}
      className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-nodoxx-muted"
    >
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}
