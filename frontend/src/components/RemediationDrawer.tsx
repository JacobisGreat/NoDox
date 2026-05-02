import { useEffect, useState } from "react";
import { AggregatorResult } from "../types";
import { exposureColor, riskBgClass } from "../utils/format";

interface Props {
  aggregator: AggregatorResult | null;
}

export default function RemediationDrawer({ aggregator }: Props) {
  const [open, setOpen] = useState(false);
  const ready = aggregator !== null;
  const count = aggregator?.remediation_list.length ?? 0;

  useEffect(() => {
    if (!ready && open) setOpen(false);
  }, [ready, open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <div className="fixed inset-x-0 bottom-0 z-30 flex justify-center px-4 pb-4 pt-2 pointer-events-none">
        <div className="pointer-events-auto border border-nodoxx-border bg-nodoxx-panel/95 px-4 py-2 backdrop-blur-md">
          <button
            type="button"
            onClick={() => ready && setOpen(true)}
            disabled={!ready}
            className="flex items-center gap-3 px-2 py-1 font-mono text-xs uppercase tracking-[0.18em] text-nodoxx-text transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
          >
            {!ready ? (
              <>
                <span
                  aria-hidden="true"
                  className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-nodoxx-dim border-t-nodoxx-text"
                />
                <span>analyzing...</span>
              </>
            ) : (
              <>
                <span className="text-nodoxx-text">$ remediation</span>
                <span className="border border-nodoxx-border bg-nodoxx-bg px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-nodoxx-text">
                  {count}
                </span>
                <ChevronUp />
              </>
            )}
          </button>
        </div>
      </div>

      <div
        aria-hidden={!open}
        onClick={() => setOpen(false)}
        className={`fixed inset-0 z-40 bg-black/70 backdrop-blur-sm transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Remediation Plan"
        className={`fixed inset-x-0 bottom-0 z-50 mx-auto w-full max-w-4xl border border-nodoxx-border bg-nodoxx-panel transition-transform duration-300 ${
          open ? "translate-y-0" : "translate-y-full"
        }`}
        style={{ maxHeight: "70vh" }}
      >
        <header className="flex items-center justify-between border-b border-nodoxx-border px-5 py-4">
          <div className="flex items-center gap-3">
            <h2 className="font-mono text-base font-semibold uppercase tracking-[0.18em] text-nodoxx-text">
              // remediation plan
            </h2>
            {aggregator && (
              <span
                className="border border-nodoxx-border bg-nodoxx-bg px-2 py-0.5 font-mono text-xs tabular-nums"
                style={{ color: exposureColor(aggregator.exposure_score) }}
              >
                {Math.round(aggregator.exposure_score)} / 100
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close remediation plan"
            className="p-1 text-nodoxx-muted transition-colors hover:bg-white/10 hover:text-nodoxx-text"
          >
            <svg
              viewBox="0 0 16 16"
              className="h-5 w-5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M3 3l10 10M13 3L3 13" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div
          className="overflow-y-auto px-5 py-4"
          style={{ maxHeight: "calc(70vh - 64px)" }}
        >
          {!aggregator || aggregator.remediation_list.length === 0 ? (
            <p className="py-12 text-center font-mono text-sm text-nodoxx-muted">
              &gt; no remediation actions recommended.
            </p>
          ) : (
            <ol className="space-y-3">
              {aggregator.remediation_list.map((item, idx) => (
                <li
                  key={idx}
                  className="flex gap-4 border border-nodoxx-border bg-nodoxx-bg p-4"
                >
                  <span className="flex-shrink-0 font-mono text-xl font-bold tabular-nums text-nodoxx-text">
                    {String(item.priority).padStart(2, "0")}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="font-mono text-sm font-semibold text-nodoxx-text">
                        {item.action}
                      </p>
                      <span
                        className={`border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] ${riskBgClass(
                          item.risk_level,
                        )}`}
                      >
                        {item.risk_level}
                      </span>
                    </div>
                    <p className="mt-1 font-mono text-sm leading-relaxed text-nodoxx-muted">
                      {item.reason}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>
    </>
  );
}

function ChevronUp() {
  return (
    <svg
      viewBox="0 0 12 12"
      className="h-3 w-3"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <path d="M2 8l4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
