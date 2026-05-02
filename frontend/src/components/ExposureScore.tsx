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
          compact ? "" : "border border-nodoxx-border bg-nodoxx-panel px-4 py-3"
        }`}
      >
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-nodoxx-dim border-t-nodoxx-text"
        />
        <div className="flex flex-col">
          <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-nodoxx-muted">
            exposure
          </span>
          <span className="font-mono text-xs text-nodoxx-muted">
            calculating...
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
        <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-nodoxx-muted">
          exposure
        </span>
        <span
          className="font-mono text-2xl font-bold tabular-nums"
          style={{ color }}
        >
          {score}
        </span>
        <span className="font-mono text-xs text-nodoxx-muted">/ 100</span>
        <span
          className="ml-1 border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[0.16em]"
          style={{ color, borderColor: color }}
        >
          {label}
        </span>
      </div>
    );
  }

  return (
    <section className="border border-nodoxx-border bg-nodoxx-panel p-6">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-nodoxx-muted">
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
        <span className="pb-2 font-mono text-base text-nodoxx-muted">
          / 100
        </span>
        <span
          className="mb-2 border px-2 py-0.5 font-mono text-xs font-semibold uppercase tracking-[0.18em]"
          style={{ color, borderColor: color }}
          aria-label={`Risk band: ${label}`}
        >
          {label}
        </span>
      </div>
      <p className="mt-2 max-w-2xl font-mono text-xs uppercase tracking-[0.18em] text-nodoxx-muted">
        {exposureBlurb(aggregator.exposure_score)}
      </p>
      <p className="mt-3 max-w-2xl font-mono text-sm leading-relaxed text-nodoxx-text/90">
        {aggregator.summary}
      </p>
    </section>
  );
}
