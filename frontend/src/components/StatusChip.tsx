import { PipelineStatus } from "../types";

interface Props {
  status: PipelineStatus;
  detail?: string | null;
}

export default function StatusChip({ status, detail }: Props) {
  const config = STATUS_CONFIG[status];
  return (
    <span
      title={detail ?? config.label}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-mono uppercase tracking-wider ${config.classes}`}
    >
      <span className={`inline-flex h-2 w-2 ${config.dot}`}>
        {config.icon}
      </span>
      <span>{config.label}</span>
    </span>
  );
}

const STATUS_CONFIG: Record<
  PipelineStatus,
  { label: string; classes: string; dot: string; icon: React.ReactNode }
> = {
  idle: {
    label: "idle",
    classes: "bg-slate-700/30 text-slate-400 border-slate-600/40",
    dot: "rounded-full bg-slate-400/70",
    icon: null,
  },
  running: {
    label: "running",
    classes:
      "bg-nodoxx-accent/15 text-nodoxx-accent border-nodoxx-accent/40 animate-running-pulse",
    dot: "rounded-full bg-nodoxx-accent",
    icon: null,
  },
  complete: {
    label: "complete",
    classes: "bg-risk-low/15 text-risk-low border-risk-low/40",
    dot: "items-center justify-center text-risk-low",
    icon: <CheckIcon />,
  },
  error: {
    label: "error",
    classes: "bg-risk-high/15 text-risk-high border-risk-high/40",
    dot: "items-center justify-center text-risk-high",
    icon: <XIcon />,
  },
  budget_exceeded: {
    label: "budget",
    classes: "bg-risk-medium/15 text-risk-medium border-risk-medium/40",
    dot: "rounded-full bg-risk-medium",
    icon: null,
  },
};

function CheckIcon() {
  return (
    <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 6.5L5 9l5-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 3l6 6M9 3l-6 6" strokeLinecap="round" />
    </svg>
  );
}
