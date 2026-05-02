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
      className={`inline-flex items-center gap-1.5 border px-2.5 py-0.5 text-[10px] font-mono uppercase tracking-[0.18em] ${config.classes}`}
    >
      <span className={`inline-flex h-2 w-2 ${config.dot}`}>{config.icon}</span>
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
    classes: "bg-transparent text-nodoxx-dim border-nodoxx-border",
    dot: "bg-nodoxx-dim",
    icon: null,
  },
  running: {
    label: "running",
    classes:
      "bg-white/[0.06] text-nodoxx-text border-nodoxx-text/60 animate-running-pulse",
    dot: "bg-nodoxx-text",
    icon: null,
  },
  complete: {
    label: "complete",
    classes: "bg-transparent text-nodoxx-text border-nodoxx-text/40",
    dot: "items-center justify-center text-nodoxx-text",
    icon: <CheckIcon />,
  },
  error: {
    label: "error",
    classes: "bg-white/10 text-nodoxx-text border-nodoxx-text",
    dot: "items-center justify-center text-nodoxx-text",
    icon: <XIcon />,
  },
  budget_exceeded: {
    label: "budget",
    classes: "bg-white/[0.04] text-nodoxx-muted border-nodoxx-muted/60",
    dot: "bg-nodoxx-muted",
    icon: null,
  },
};

function CheckIcon() {
  return (
    <svg
      viewBox="0 0 12 12"
      className="h-2.5 w-2.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <path d="M2 6.5L5 9l5-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg
      viewBox="0 0 12 12"
      className="h-2.5 w-2.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <path d="M3 3l6 6M9 3l-6 6" strokeLinecap="round" />
    </svg>
  );
}
