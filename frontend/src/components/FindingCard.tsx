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

export default function FindingCard({ finding }: Props) {
  const borderColor = riskBorderColor(finding.risk_level);
  const confColor = confidenceColor(finding.confidence);
  const confidencePct = Math.max(0, Math.min(1, finding.confidence)) * 100;

  return (
    <article
      className="animate-card-in rounded-lg bg-nodoxx-panel"
      style={{
        borderLeft: `3px solid ${borderColor}`,
        boxShadow: "0 0 0 1px rgba(31,63,99,0.18)",
      }}
    >
      <div className="space-y-3 p-4">
        <header className="flex items-start justify-between gap-3">
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-nodoxx-muted">
            {finding.source}
          </span>
          <span
            className={`shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${riskBgClass(
              finding.risk_level
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
                className="flex gap-2 text-sm leading-relaxed text-nodoxx-text/90"
              >
                <span aria-hidden="true" className="mt-1.5 inline-block h-1 w-1 shrink-0 rounded-full bg-nodoxx-accent/70" />
                <span className="min-w-0 break-words">{renderEvidence(item)}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-center gap-3">
          <span className="text-[10px] font-mono uppercase tracking-wider text-nodoxx-muted/70">
            confidence
          </span>
          <span
            className="font-mono text-sm font-semibold tabular-nums"
            style={{ color: confColor }}
          >
            {formatConfidence(finding.confidence)}
          </span>
          <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-nodoxx-bg">
            <div
              className="h-full rounded-full transition-[width] duration-500"
              style={{ width: `${confidencePct}%`, backgroundColor: confColor }}
            />
          </div>
        </div>

        <div
          className="rounded-md border-l-2 border-nodoxx-accent/70 bg-[#0d1525] px-3 py-2 text-sm text-nodoxx-text/90"
        >
          <span className="mb-0.5 block text-[10px] font-mono uppercase tracking-wider text-nodoxx-accent/80">
            remediation
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
          className="font-mono text-nodoxx-accent underline decoration-nodoxx-accent/40 underline-offset-2 hover:decoration-nodoxx-accent break-all"
        >
          {seg.value}
        </a>
      );
    }
    return <span key={idx}>{seg.value}</span>;
  });
}
