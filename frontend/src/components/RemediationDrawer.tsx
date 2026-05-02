import { useEffect, useState } from "react";
import { AggregatorResult } from "../types";
import { riskBgClass } from "../utils/format";

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
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center px-4 pb-5 pt-2">
        <div className="pointer-events-auto border border-kali-border bg-kali-surface px-5 py-3">
          <button
            type="button"
            onClick={() => ready && setOpen(true)}
            disabled={!ready}
            className="flex items-center gap-4 px-2 py-1 font-mono text-[14px] uppercase tracking-label text-kali-text transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
          >
            {!ready ? (
              <>
                <span aria-hidden="true" className="font-mono text-kali-text animate-running-pulse">
                  [...]
                </span>
                <span>still putting your action plan together</span>
              </>
            ) : (
              <>
                <span>$ next steps</span>
                <span className="border border-kali-border bg-kali-bg px-2 py-0.5 font-mono text-[12px] tabular-nums text-kali-text">
                  {count}
                </span>
                <span aria-hidden="true" className="text-[16px]">
                  [^]
                </span>
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
        aria-label="Next Steps"
        className={`fixed inset-x-0 bottom-0 z-50 mx-auto w-full max-w-5xl border border-kali-border bg-kali-surface transition-transform duration-300 ${
          open ? "translate-y-0" : "translate-y-full"
        }`}
        style={{ maxHeight: "82vh" }}
      >
        <header className="flex items-start justify-between gap-4 border-b border-kali-border px-7 py-6">
          <div className="min-w-0">
            <h2 className="font-mono text-[20px] font-bold uppercase tracking-label text-kali-text">
              // next steps
            </h2>
            <p className="mt-2 font-mono text-[14px] leading-relaxed text-kali-dim">
              Here's what to clean up, in order. Start at the top — that's the
              one we'd tackle first.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close next steps"
            className="shrink-0 px-3 py-1 font-mono text-[14px] uppercase tracking-label text-kali-dim transition-colors hover:text-kali-text"
          >
            [X]
          </button>
        </header>

        <div
          className="overflow-y-auto px-7 py-6"
          style={{ maxHeight: "calc(82vh - 116px)" }}
        >
          {!aggregator || aggregator.remediation_list.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-20 text-center">
              <p className="font-mono text-[16px] text-kali-text">
                Nothing urgent jumped out.
              </p>
              <p className="max-w-md font-mono text-[13px] leading-relaxed text-kali-dim">
                Your public footprint looks pretty clean. Re-run the audit
                anytime you change something so we can spot anything new.
              </p>
            </div>
          ) : (
            <ol className="space-y-4">
              {aggregator.remediation_list.map((item, idx) => (
                <li
                  key={idx}
                  className="flex gap-6 border border-kali-border bg-kali-bg p-6"
                >
                  <div className="flex shrink-0 flex-col items-start gap-1">
                    <span className="font-mono text-[10px] uppercase tracking-label text-kali-label">
                      step
                    </span>
                    <span className="font-mono text-[36px] font-bold leading-none tabular-nums text-kali-text">
                      {String(item.priority).padStart(2, "0")}
                    </span>
                  </div>
                  <div className="min-w-0 flex-1 space-y-3">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <p className="whitespace-pre-line font-mono text-[16px] font-bold leading-snug text-kali-text">
                        {item.action}
                      </p>
                      <span
                        className={`shrink-0 border px-2.5 py-1 font-mono text-[11px] font-bold uppercase tracking-label ${riskBgClass(
                          item.risk_level,
                        )}`}
                      >
                        {item.risk_level}
                      </span>
                    </div>
                    <p className="font-mono text-[14px] leading-relaxed text-kali-dim">
                      <span className="mr-1.5 font-mono text-[10px] uppercase tracking-label text-kali-label">
                        why
                      </span>
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
