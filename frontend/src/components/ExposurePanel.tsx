interface ExposurePanelProps {
  gatePassed: boolean;
}

export default function ExposurePanel({ gatePassed }: ExposurePanelProps) {
  return (
    <section className="rounded-lg border border-nodox-border bg-nodox-panel/60 p-5">
      <h2 className="m-0 text-sm font-semibold uppercase tracking-normal text-nodox-cyan">
        Exposure Summary
      </h2>

      <div className="mt-3 rounded border border-nodox-border bg-black/20 p-4">
        <div className="text-xs text-slate-400">Top-Level Exposure Score</div>
        <div className="mt-1 text-3xl font-semibold text-slate-100">{gatePassed ? "--" : "--"}</div>
        <div className="mt-2 text-xs text-slate-400">
          Score aggregation activates after all three pipelines are implemented and complete.
        </div>
      </div>

      <div className="mt-4 rounded border border-nodox-border bg-black/20 p-4">
        <div className="text-xs font-medium text-slate-200">Risk Badge Legend</div>
        <div className="mt-3 flex flex-wrap gap-2 text-[10px]">
          <span className="rounded border border-nodox-high/70 bg-nodox-high/15 px-2 py-1 text-red-200">
            HIGH
          </span>
          <span className="rounded border border-nodox-medium/70 bg-nodox-medium/15 px-2 py-1 text-orange-200">
            MEDIUM
          </span>
          <span className="rounded border border-nodox-low/70 bg-nodox-low/15 px-2 py-1 text-green-200">
            LOW
          </span>
        </div>
      </div>

      <div className="mt-4 rounded border border-nodox-border bg-black/20 p-4">
        <div className="text-xs font-medium text-slate-200">Remediation Panel</div>
        <p className="mt-2 text-xs text-slate-400">
          Consolidated action items will appear here once findings start streaming in.
        </p>
      </div>
    </section>
  );
}
