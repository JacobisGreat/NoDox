import { Finding } from "../types";
import {
  confidenceColor,
  formatConfidence,
  riskBgClass,
  riskBorderColor,
  splitUrls,
} from "../utils/format";

interface Props {
  finding: Finding;
}

// Pre-attentive severity encoding inside a B&W constraint. Brightness alone
// (a single channel) doesn't make HIGH/CRITICAL pop in a scrolling list, so
// we widen the encoding across border-thickness, surface-tint, and source-
// label brightness. Treisman's pre-attentive feature theory: the eye locks
// onto the thicker bar + brighter surface in <200ms without reading.
const SEVERITY_TREATMENT: Record<
  Finding["risk_level"],
  { borderWidth: number; panelClass: string; sourceClass: string }
> = {
  LOW: {
    borderWidth: 2,
    panelClass: "border border-nodoxx-border bg-nodoxx-panel2",
    sourceClass: "text-nodoxx-muted",
  },
  MEDIUM: {
    borderWidth: 3,
    panelClass: "border border-nodoxx-border bg-nodoxx-panel2",
    sourceClass: "text-nodoxx-muted",
  },
  HIGH: {
    borderWidth: 4,
    panelClass: "border border-nodoxx-border bg-white/[0.03]",
    sourceClass: "text-nodoxx-text",
  },
  CRITICAL: {
    borderWidth: 5,
    panelClass: "border border-nodoxx-text/40 bg-white/[0.05]",
    sourceClass: "text-nodoxx-text",
  },
};

export default function FindingCard({ finding }: Props) {
  const borderColor = riskBorderColor(finding.risk_level);
  const confColor = confidenceColor(finding.confidence);
  const confidencePct = Math.max(0, Math.min(1, finding.confidence)) * 100;
  const treat =
    SEVERITY_TREATMENT[finding.risk_level] ?? SEVERITY_TREATMENT.MEDIUM;

  return (
    <article
      className={`animate-card-in ${treat.panelClass}`}
      style={{ borderLeft: `${treat.borderWidth}px solid ${borderColor}` }}
    >
      <div className="space-y-3 p-4">
        <header className="flex items-start justify-between gap-3">
          <span
            className={`font-mono text-[11px] font-semibold uppercase tracking-[0.18em] ${treat.sourceClass}`}
          >
            // {finding.source}
          </span>
          <span
            className={`shrink-0 border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] ${riskBgClass(
              finding.risk_level,
            )}`}
          >
            {finding.risk_level}
          </span>
        </header>

        {finding.evidence_chain.length > 0 && (
          <ul className="space-y-1.5">
            {finding.evidence_chain.map((item, idx) => (
              <li
                key={idx}
                className="flex gap-2 font-mono text-[13px] leading-relaxed text-nodoxx-text/90"
              >
                <span
                  aria-hidden="true"
                  className="select-none pt-0.5 font-mono text-xs text-nodoxx-muted"
                >
                  &gt;
                </span>
                <span className="min-w-0 break-words">
                  {renderEvidence(item)}
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-center gap-3">
          <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-nodoxx-muted">
            conf
          </span>
          <span
            className="font-mono text-sm font-semibold tabular-nums"
            style={{ color: confColor }}
          >
            {formatConfidence(finding.confidence)}
          </span>
          <div className="relative h-[3px] flex-1 overflow-hidden bg-nodoxx-bg">
            <div
              className="h-full transition-[width] duration-500"
              style={{ width: `${confidencePct}%`, backgroundColor: confColor }}
            />
          </div>
        </div>

        <div className="border-l-2 border-nodoxx-text/70 bg-nodoxx-bg px-3 py-2 font-mono text-[13px] text-nodoxx-text/90">
          <span className="mb-0.5 block font-mono text-[10px] uppercase tracking-[0.2em] text-nodoxx-muted">
            $ remediation
          </span>
          <span className="leading-relaxed">{finding.remediation}</span>
        </div>
      </div>
    </article>
  );
}

function renderEvidence(text: string) {
  const segments = splitUrls(text);
  return segments.map((seg, idx) => {
    if (seg.type === "url") {
      return (
        <a
          key={idx}
          href={seg.value}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-nodoxx-text underline decoration-nodoxx-muted underline-offset-2 hover:decoration-nodoxx-text break-all"
        >
          {seg.value}
        </a>
      );
    }
    return <span key={idx}>{seg.value}</span>;
  });
}
