import { FormEvent, useEffect, useState } from "react";
import { fetchProfile } from "../api";
import { FetchProfileError } from "../types";

const SUBTITLE = "see what strangers can find about you on instagram.";
const TYPE_INTERVAL_MS = 24;

// Static ASCII banner. Pre-rendered (not animated) so it stays crisp and
// doesn't compete with the typewriter or matrix rain for attention.
const BANNER = String.raw`
 _   _  ___  ____   ___ __  __
| \ | |/ _ \|  _ \ / _ \\ \/ /
|  \| | | | | | | | | | |\  /
| |\  | |_| | |_| | |_| |/  \
|_| \_|\___/|____/ \___//_/\_\
`.trim();

function useTypewriter(text: string, intervalMs: number): string {
  const [shown, setShown] = useState("");
  useEffect(() => {
    setShown("");
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      setShown(text.slice(0, i));
      if (i >= text.length) window.clearInterval(id);
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [text, intervalMs]);
  return shown;
}

export default function LandingPage() {
  const [username, setUsername] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = username.trim().replace(/^@+/, "");
  const canSubmit = trimmed.length > 0 && !submitting;

  const typed = useTypewriter(SUBTITLE, TYPE_INTERVAL_MS);
  const typedDone = typed.length === SUBTITLE.length;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const result = await fetchProfile(trimmed);
      window.location.pathname = `/audit/${result.session_id}`;
    } catch (err) {
      if (err instanceof FetchProfileError) {
        if (err.kind === "not_found") {
          setError("profile not found. check the username.");
        } else if (err.kind === "private") {
          setError("private profile. nodoxx only audits public targets.");
        } else if (err.kind === "rate_limited") {
          setError("instagram rate limit. retry in a minute.");
        } else if (err.kind === "validation") {
          setError(err.message?.toLowerCase() || "invalid username.");
        } else {
          setError("something went wrong. retry.");
        }
      } else {
        setError("something went wrong. retry.");
      }
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-[100dvh] w-full flex items-center justify-center px-4 py-10">
      <section className="relative w-full max-w-2xl border border-nodoxx-border bg-nodoxx-bg/85 backdrop-blur-sm shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_24px_60px_-20px_rgba(0,0,0,0.8)]">
        {/* Terminal title bar — fake window chrome, flat, monochrome. */}
        <div className="flex items-center justify-between border-b border-nodoxx-border px-4 py-2 font-mono text-[10px] uppercase tracking-[0.22em] text-nodoxx-muted">
          <span className="flex items-center gap-2">
            <span className="h-2 w-2 border border-nodoxx-muted/60" />
            <span className="h-2 w-2 border border-nodoxx-muted/60" />
            <span className="h-2 w-2 border border-nodoxx-muted/60" />
          </span>
          <span>nodoxx@local: ~/audit</span>
          <span aria-hidden="true">[ssh]</span>
        </div>

        <div className="px-6 py-8 sm:px-10 sm:py-10">
          {/* ASCII-art banner. Hidden on narrow phones where it would wrap
              unattractively; the typed subtitle + corner caret carry the
              brand on those breakpoints. */}
          <pre
            aria-label="NODOXX"
            className="hidden sm:block whitespace-pre font-mono text-[11px] leading-[1.05] font-bold text-nodoxx-text select-none"
          >
            {BANNER}
          </pre>
          <h1 className="sm:hidden font-mono font-bold text-nodoxx-text tracking-[0.22em] text-4xl select-none">
            NODOXX
          </h1>

          <div className="mt-5 font-mono text-xs sm:text-sm leading-relaxed text-nodoxx-muted">
            <span className="text-nodoxx-text">$ </span>
            <span className="text-nodoxx-text">./nodoxx --help</span>
          </div>
          <p className="mt-1 font-mono text-xs sm:text-sm leading-relaxed text-nodoxx-text/90 min-h-[1.5em]">
            {typed}
            <span
              aria-hidden="true"
              className={`ml-0.5 inline-block h-[0.85em] w-[0.5ch] -translate-y-[0.06em] bg-nodoxx-text align-middle ${
                typedDone ? "animate-caret-blink" : ""
              }`}
            />
          </p>
          <p className="mt-1 font-mono text-[11px] leading-relaxed text-nodoxx-muted">
            consent-based osint self-audit &middot; identity &middot;
            geolocation &middot; web footprint
          </p>

          <form onSubmit={onSubmit} className="mt-8 space-y-3">
            <label className="block">
              <span className="mb-1.5 block font-mono text-[10px] uppercase tracking-[0.22em] text-nodoxx-muted">
                [target.username]
              </span>
              <div className="flex items-center border border-nodoxx-border bg-nodoxx-panel px-3 transition-colors focus-within:border-nodoxx-text">
                <span className="select-none pr-2 font-mono text-base text-nodoxx-muted">
                  &gt;
                </span>
                <input
                  type="text"
                  inputMode="text"
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="username"
                  disabled={submitting}
                  className="w-full bg-transparent py-3 pr-2 font-mono text-base text-nodoxx-text placeholder-nodoxx-dim outline-none disabled:opacity-60"
                />
              </div>
            </label>

            <button
              type="submit"
              disabled={!canSubmit}
              className="flex w-full items-center justify-center gap-3 border border-nodoxx-text bg-nodoxx-text py-3 px-4 font-mono text-sm font-semibold uppercase tracking-[0.22em] text-nodoxx-bg transition-[background-color,color,border-color] hover:bg-nodoxx-bg hover:text-nodoxx-text active:translate-y-[1px] disabled:cursor-not-allowed disabled:border-nodoxx-border disabled:bg-transparent disabled:text-nodoxx-dim disabled:active:translate-y-0"
            >
              {submitting ? (
                <>
                  <Spinner />
                  <span>fetching...</span>
                </>
              ) : (
                <span>./run_audit</span>
              )}
            </button>

            {error ? (
              <p
                className="border border-nodoxx-text/40 bg-white/[0.04] px-3 py-2 font-mono text-xs text-nodoxx-text"
                role="alert"
              >
                <span className="text-nodoxx-muted">err: </span>
                {error}
              </p>
            ) : null}
          </form>

          <footer className="mt-8 flex items-center justify-between border-t border-nodoxx-border pt-4 font-mono text-[10px] uppercase tracking-[0.2em] text-nodoxx-muted">
            <span>// public data, with consent</span>
            <span aria-hidden="true">[ enter ]</span>
          </footer>
        </div>
      </section>
    </main>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-nodoxx-bg/30 border-t-nodoxx-bg"
    />
  );
}
