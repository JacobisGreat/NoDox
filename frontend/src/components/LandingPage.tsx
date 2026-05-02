import { FormEvent, useEffect, useState } from "react";
import { fetchProfile } from "../api";
import { FetchProfileError } from "../types";

const SUBTITLE = "see what strangers can find about you on instagram.";
const TYPE_INTERVAL_MS = 24;

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
    <main className="flex min-h-[calc(100dvh-32px)] w-full items-center justify-center px-4 py-10 sm:px-6">
      <section className="w-full max-w-2xl border border-kali-border bg-kali-surface">
        <div className="flex items-center justify-between border-b border-kali-border px-4 py-2 font-mono text-[10px] uppercase tracking-label text-kali-label">
          <span>nodoxx@local: ~/audit</span>
          <span aria-hidden="true">[ssh]</span>
        </div>

        <div className="px-6 py-8 sm:px-10 sm:py-10">
          <pre
            aria-label="NODOXX"
            className="hidden sm:block whitespace-pre font-mono text-[11px] leading-[1.05] font-bold text-kali-text select-none"
          >
            {BANNER}
          </pre>
          <h1 className="sm:hidden font-mono text-4xl font-bold tracking-label text-kali-text select-none">
            NODOXX
          </h1>

          <div className="mt-6 font-mono text-[13px] leading-relaxed text-kali-text">
            <span>$ </span>
            <span>./nodoxx --help</span>
          </div>
          <p className="mt-1 min-h-[1.5em] font-mono text-[13px] leading-relaxed text-kali-text">
            {typed}
            <span
              aria-hidden="true"
              className={`ml-0.5 inline-block h-[0.85em] w-[0.5ch] bg-kali-text align-middle ${
                typedDone ? "animate-caret-blink" : ""
              }`}
            />
          </p>
          <p className="mt-2 font-mono text-[11px] leading-relaxed uppercase tracking-label text-kali-label">
            consent-based osint self-audit &middot; identity &middot;
            geolocation &middot; web footprint
          </p>

          <form onSubmit={onSubmit} className="mt-8 space-y-3">
            <label className="block">
              <span className="mb-2 block font-mono text-[10px] uppercase tracking-label text-kali-label">
                [target.username]
              </span>
              <div className="flex h-9 items-center rounded-input border border-kali-border bg-kali-bg px-3 transition-colors focus-within:border-kali-text">
                <span className="select-none pr-2 font-mono text-[13px] text-kali-dim">
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
                  className="h-full w-full bg-transparent font-mono text-[13px] text-kali-text placeholder-kali-label outline-none disabled:opacity-60"
                />
              </div>
            </label>

            <button
              type="submit"
              disabled={!canSubmit}
              className="flex h-9 w-full items-center justify-center gap-2 border border-kali-text bg-kali-text px-3 font-mono text-[13px] font-bold uppercase tracking-label text-kali-bg transition-colors hover:bg-kali-bg hover:text-kali-text disabled:cursor-not-allowed disabled:border-kali-border disabled:bg-kali-bg disabled:text-kali-label"
            >
              {submitting ? <span>[...] fetching</span> : <span>$ ./run_audit</span>}
            </button>

            {error ? (
              <p
                className="border border-kali-border bg-kali-bg px-3 py-2 font-mono text-[13px] text-kali-text"
                role="alert"
              >
                <span className="text-kali-label">[err] </span>
                {error}
              </p>
            ) : null}
          </form>

          <footer className="mt-8 flex items-center justify-between border-t border-kali-border pt-4 font-mono text-[10px] uppercase tracking-label text-kali-label">
            <span>// public data, with consent</span>
            <span aria-hidden="true">[ enter ]</span>
          </footer>
        </div>
      </section>
    </main>
  );
}
