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
    <header className="sticky top-0 z-30 border-b border-kali-border bg-kali-bg">
      <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-3 px-4 py-3 sm:px-6 lg:flex-row lg:items-center lg:gap-6">
        <div className="flex min-w-0 items-center gap-3">
          <a
            href="/"
            className="select-none font-mono text-base font-bold tracking-label text-kali-text hover:text-kali-dim"
          >
            NODOXX
          </a>
          <span className="hidden h-5 w-px bg-kali-border sm:block" />
          {profile ? <ProfileBlock profile={profile} /> : <ProfileSkeleton />}
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 lg:ml-auto">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-xl font-bold tabular-nums text-kali-text">
              {totalFindings}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
              findings
            </span>
          </div>

          <ExposureScore aggregator={aggregator} compact />

          <CostDisplay cost={cost} tick={costTick} />

          <ConnectionToken connected={connected} streamDone={streamDone} />
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-[1280px] flex-wrap items-center gap-3 px-4 pb-3 sm:px-6">
        {PIPELINES.map((p) => (
          <div key={p} className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
              [{pipelineTitle(p)}]
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
        className="h-10 w-10 flex-shrink-0 border border-kali-border bg-kali-surface object-cover grayscale"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
        }}
      />
      <div className="flex min-w-0 flex-col leading-tight">
        <span className="truncate font-mono text-[13px] text-kali-text">
          @{profile.username}
        </span>
        <span className="font-mono text-[11px] text-kali-dim tabular-nums">
          {formatCount(profile.followers)} followers
        </span>
      </div>
    </div>
  );
}

function ProfileSkeleton() {
  return (
    <div className="flex items-center gap-3">
      <div className="h-10 w-10 animate-running-pulse bg-kali-surface" />
      <div className="flex flex-col gap-1">
        <div className="h-3 w-24 animate-running-pulse bg-kali-surface" />
        <div className="h-2 w-16 animate-running-pulse bg-kali-surface" />
      </div>
    </div>
  );
}

function ConnectionToken({
  connected,
  streamDone,
}: {
  connected: boolean;
  streamDone: boolean;
}) {
  let token = "[ ]";
  let label = "connecting";
  let cls = "text-kali-label";
  if (streamDone) {
    token = "[OK]";
    label = "done";
    cls = "text-kali-text";
  } else if (connected) {
    token = "[*]";
    label = "live";
    cls = "text-kali-text animate-running-pulse";
  }
  return (
    <span
      title={label}
      className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-label text-kali-label"
    >
      <span className={cls}>{token}</span>
      {label}
    </span>
  );
}
