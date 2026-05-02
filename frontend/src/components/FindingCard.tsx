import { Finding } from "../types";
import {
  confidenceColor,
  formatConfidence,
  riskBgClass,
  severityPrefix,
  splitUrls,
} from "../utils/format";

interface Props {
  finding: Finding;
}

const SOURCE_CLASS: Record<Finding["risk_level"], string> = {
  LOW: "text-kali-dim",
  MEDIUM: "text-kali-dim",
  HIGH: "text-kali-text",
  CRITICAL: "text-kali-text",
};

export default function FindingCard({ finding }: Props) {
  const confColor = confidenceColor(finding.confidence);
  const confidencePct = Math.max(0, Math.min(1, finding.confidence)) * 100;

  return (
    <article className="border border-kali-border bg-kali-surface transition-colors hover:border-kali-text">
      <div className="space-y-3 p-4">
        <header className="flex items-start justify-between gap-3">
          <span
            className={`font-mono text-[11px] font-bold uppercase tracking-label ${SOURCE_CLASS[finding.risk_level]}`}
          >
            {severityPrefix(finding.risk_level)} // {finding.source}
          </span>
          <span
            className={`shrink-0 border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-label ${riskBgClass(finding.risk_level)}`}
          >
            {finding.risk_level}
          </span>
        </header>

        {finding.evidence_chain.length > 0 && (
          <ul className="space-y-1.5">
            {finding.evidence_chain.map((item, idx) => (
              <li
                key={idx}
                className="flex gap-2 font-mono text-[13px] leading-relaxed text-kali-text"
              >
                <span
                  aria-hidden="true"
                  className="select-none pt-0.5 font-mono text-kali-dim"
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
          <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
            conf
          </span>
          <span
            className="font-mono text-[13px] font-bold tabular-nums"
            style={{ color: confColor }}
          >
            {formatConfidence(finding.confidence)}
          </span>
          <div className="relative h-[3px] flex-1 overflow-hidden bg-kali-bg">
            <div
              className="h-full transition-[width] duration-500"
              style={{ width: `${confidencePct}%`, backgroundColor: confColor }}
            />
          </div>
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
          className="font-mono text-kali-text underline decoration-kali-dim underline-offset-2 hover:decoration-kali-text break-all"
        >
          {seg.value}
        </a>
      );
    }
    return <span key={idx}>{seg.value}</span>;
  });
}
