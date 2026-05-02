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
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center px-4 pb-4 pt-2">
        <div className="pointer-events-auto border border-kali-border bg-kali-surface px-4 py-2">
          <button
            type="button"
            onClick={() => ready && setOpen(true)}
            disabled={!ready}
            className="flex items-center gap-3 px-2 py-1 font-mono text-[11px] uppercase tracking-label text-kali-text transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
          >
            {!ready ? (
              <>
                <span aria-hidden="true" className="font-mono text-kali-text animate-running-pulse">[...]</span>
                <span>analyzing</span>
              </>
            ) : (
              <>
                <span>$ remediation</span>
                <span className="border border-kali-border bg-kali-bg px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-kali-text">
                  {count}
                </span>
                <span aria-hidden="true">[^]</span>
              </>
            )}
          </button>
        </div>
      </div>

      <div
        aria-hidden={!open}
        onClick={() => setOpen(false)}
        className={`fixed inset-0 z-40 bg-black/70 transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Remediation Plan"
        className={`fixed inset-x-0 bottom-0 z-50 mx-auto w-full max-w-4xl border border-kali-border bg-kali-surface transition-transform duration-300 ${
          open ? "translate-y-0" : "translate-y-full"
        }`}
        style={{ maxHeight: "70vh" }}
      >
        <header className="flex items-center justify-between border-b border-kali-border px-5 py-4">
          <div className="flex items-center gap-3">
            <h2 className="font-mono text-[13px] font-bold uppercase tracking-label text-kali-text">
              // remediation plan
            </h2>
            {aggregator && (
              <span
                className="border border-kali-border bg-kali-bg px-2 py-0.5 font-mono text-[11px] tabular-nums"
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
            className="px-2 py-1 font-mono text-[11px] uppercase tracking-label text-kali-dim transition-colors hover:text-kali-text"
          >
            [X]
          </button>
        </header>

        <div
          className="overflow-y-auto px-5 py-4"
          style={{ maxHeight: "calc(70vh - 64px)" }}
        >
          {!aggregator || aggregator.remediation_list.length === 0 ? (
            <p className="py-12 text-center font-mono text-[13px] text-kali-dim">
              &gt; no remediation actions recommended.
            </p>
          ) : (
            <ol className="space-y-3">
              {aggregator.remediation_list.map((item, idx) => (
                <li
                  key={idx}
                  className="flex gap-4 border border-kali-border bg-kali-bg p-4"
                >
                  <span className="flex-shrink-0 font-mono text-xl font-bold tabular-nums text-kali-text">
                    {String(item.priority).padStart(2, "0")}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="font-mono text-[13px] font-bold text-kali-text">
                        {item.action}
                      </p>
                      <span
                        className={`border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-label ${riskBgClass(
                          item.risk_level,
                        )}`}
                      >
                        {item.risk_level}
                      </span>
                    </div>
                    <p className="prose mt-2 text-[14px] text-kali-dim">
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
