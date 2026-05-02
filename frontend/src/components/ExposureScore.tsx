import { AggregatorResult } from "../types";
import { exposureColor } from "../utils/format";

interface Props {
  aggregator: AggregatorResult | null;
  compact?: boolean;
}

export default function ExposureScore({ aggregator, compact = false }: Props) {
  if (!aggregator) {
    return (
      <div
        className={`flex items-center gap-3 ${
          compact ? "" : "rounded-lg border border-nodoxx-border/40 bg-nodoxx-panel px-4 py-3"
        }`}
      >
        <span
          aria-hidden="true"
          className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-nodoxx-muted/30 border-t-nodoxx-accent"
        />
        <div className="flex flex-col">
          <span className="text-[10px] font-mono uppercase tracking-wider text-nodoxx-muted/70">
            exposure
          </span>
          <span className="text-xs text-nodoxx-muted">Calculating...</span>
        </div>
      </div>
    );
  }

  const color = exposureColor(aggregator.exposure_score);
  if (compact) {
    return (
      <div className="flex items-baseline gap-2">
        <span className="text-[10px] font-mono uppercase tracking-wider text-nodoxx-muted/70">
          exposure
        </span>
        <span className="font-mono text-2xl font-bold tabular-nums" style={{ color }}>
          {Math.round(aggregator.exposure_score)}
        </span>
        <span className="font-mono text-xs text-nodoxx-muted">/ 100</span>
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-nodoxx-border/40 bg-nodoxx-panel p-6">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-mono uppercase tracking-wider text-nodoxx-muted/70">
          exposure score
        </span>
      </div>
      <div className="mt-2 flex items-end gap-3">
        <span
          className="font-mono font-bold tabular-nums leading-none"
          style={{ color, fontSize: "72px" }}
        >
          {Math.round(aggregator.exposure_score)}
        </span>
        <span className="pb-2 font-mono text-base text-nodoxx-muted">/ 100</span>
      </div>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-nodoxx-text/90">
        {aggregator.summary}
      </p>
    </section>
  );
}
