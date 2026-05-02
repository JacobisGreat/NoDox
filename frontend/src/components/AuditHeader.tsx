import { AggregatorResult, Profile } from "../types";
import { formatCount } from "../utils/format";
import ExposureScore from "./ExposureScore";

interface Props {
  profile: Profile | null;
  totalFindings: number;
  aggregator: AggregatorResult | null;
}

export default function AuditHeader({
  profile,
  totalFindings,
  aggregator,
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
        </div>
      </div>
    </header>
  );
}

function ProfileBlock({ profile }: { profile: Profile }) {
  return (
    <div className="flex min-w-0 flex-col leading-tight">
      <span className="truncate font-mono text-[13px] text-kali-text">
        @{profile.username}
      </span>
      <span className="font-mono text-[11px] text-kali-dim tabular-nums">
        {formatCount(profile.followers)} followers
      </span>
    </div>
  );
}

function ProfileSkeleton() {
  return (
    <div className="flex flex-col gap-1">
      <div className="h-3 w-24 animate-running-pulse bg-kali-surface" />
      <div className="h-2 w-16 animate-running-pulse bg-kali-surface" />
    </div>
  );
}
