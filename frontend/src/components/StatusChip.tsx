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
      className={`inline-flex items-center gap-1.5 border px-2 py-0.5 font-mono text-[10px] uppercase tracking-label ${config.classes}`}
    >
      <span className="font-mono">{config.token}</span>
      <span>{config.label}</span>
    </span>
  );
}

const STATUS_CONFIG: Record<
  PipelineStatus,
  { label: string; classes: string; token: string }
> = {
  idle: {
    label: "idle",
    classes: "text-kali-label border-kali-border",
    token: "[ ]",
  },
  running: {
    label: "running",
    classes: "text-kali-text border-kali-text animate-running-pulse",
    token: "[~]",
  },
  complete: {
    label: "complete",
    classes: "text-kali-text border-kali-border",
    token: "[OK]",
  },
  error: {
    label: "error",
    classes: "text-kali-text border-kali-text",
    token: "[ERR]",
  },
  budget_exceeded: {
    label: "budget",
    classes: "text-kali-dim border-kali-border",
    token: "[$]",
  },
};
