interface PipelineStatusPanelProps {
  gatePassed: boolean;
}

type PipelineState = "locked" | "ready";

interface PipelineSummary {
  key: string;
  title: string;
  description: string;
  state: PipelineState;
}

function stateStyle(state: PipelineState): string {
  if (state === "ready") return "border-nodox-low text-green-300";
  return "border-nodox-medium text-orange-200";
}

function stateLabel(state: PipelineState): string {
  if (state === "ready") return "READY";
  return "LOCKED";
}

export default function PipelineStatusPanel({ gatePassed }: PipelineStatusPanelProps) {
  const pipelines: PipelineSummary[] = [
    {
      key: "geo",
      title: "Pipeline 2 · Geolocation",
      description: "EXIF + visual geolocation + VLM reasoning",
      state: gatePassed ? "ready" : "locked"
    },
    {
      key: "identity",
      title: "Pipeline 1 · Identity Cross-Reference",
      description: "Blackbird + Sherlock + similarity correlation",
      state: gatePassed ? "ready" : "locked"
    },
    {
      key: "web",
      title: "Pipeline 3 · Web Footprint / Dorking",
      description: "SpiderFoot + DorkER + Google CSE",
      state: gatePassed ? "ready" : "locked"
    }
  ];

  return (
    <section className="rounded-lg border border-nodox-border bg-nodox-panel/60 p-5">
      <h2 className="m-0 text-sm font-semibold uppercase tracking-normal text-nodox-cyan">
        Pipeline Orchestration
      </h2>
      <p className="mb-4 mt-3 text-xs text-slate-300">
        Three async pipelines will run concurrently once OAuth gate is satisfied.
      </p>

      <div className="space-y-3">
        {pipelines.map((pipeline) => (
          <article
            key={pipeline.key}
            className="rounded border border-nodox-border bg-black/20 p-3 text-xs text-slate-200"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="font-medium">{pipeline.title}</div>
              <span className={`rounded border px-2 py-1 text-[10px] ${stateStyle(pipeline.state)}`}>
                {stateLabel(pipeline.state)}
              </span>
            </div>
            <div className="mt-2 text-slate-400">{pipeline.description}</div>
            <div className="mt-3 h-1.5 w-full rounded bg-slate-900">
              <div
                className={`h-1.5 rounded ${
                  pipeline.state === "ready" ? "w-full bg-nodox-low" : "w-[12%] bg-nodox-medium"
                }`}
              />
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
