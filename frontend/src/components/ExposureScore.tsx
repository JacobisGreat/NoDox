import { AggregatorResult } from "../types";
import { exposureBlurb, exposureColor, exposureLabel } from "../utils/format";

interface Props {
  aggregator: AggregatorResult | null;
  compact?: boolean;
}

export default function ExposureScore({ aggregator, compact = false }: Props) {
  if (!aggregator) {
    return (
      <div
        className={`flex items-center gap-3 ${
          compact ? "" : "border border-kali-border bg-kali-surface px-4 py-3"
        }`}
      >
        <span
          aria-hidden="true"
          className="font-mono text-[13px] text-kali-text animate-running-pulse"
        >
          [...]
        </span>
        <div className="flex flex-col">
          <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
            exposure
          </span>
          <span className="font-mono text-[11px] text-kali-dim">
            calculating
          </span>
        </div>
      </div>
    );
  }

  const score = Math.round(aggregator.exposure_score);
  const color = exposureColor(aggregator.exposure_score);
  const label = exposureLabel(aggregator.exposure_score);

  if (compact) {
    return (
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
          exposure
        </span>
        <span
          className="font-mono text-2xl font-bold tabular-nums"
          style={{ color }}
        >
          {score}
        </span>
        <span className="font-mono text-[11px] text-kali-dim">/ 100</span>
        <span
          className="ml-1 border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-label"
          style={{ color, borderColor: color }}
        >
          {label}
        </span>
      </div>
    );
  }

  return (
    <section className="border border-kali-border bg-kali-surface p-6 transition-colors hover:border-kali-text">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
          // exposure score
        </span>
      </div>
      <div className="mt-2 flex items-end gap-4">
        <span
          className="font-mono font-bold tabular-nums leading-none"
          style={{ color, fontSize: "72px" }}
        >
          {score}
        </span>
        <span className="pb-2 font-mono text-base text-kali-dim">
          / 100
        </span>
        <span
          className="mb-2 border px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-label"
          style={{ color, borderColor: color }}
          aria-label={`Risk band: ${label}`}
        >
          {label}
        </span>
      </div>
      <p className="mt-3 max-w-2xl font-mono text-[11px] uppercase tracking-label text-kali-label">
        {exposureBlurb(aggregator.exposure_score)}
      </p>
      <p className="prose mt-4 text-[14px] text-kali-text">
        {aggregator.summary}
      </p>
    </section>
  );
}
