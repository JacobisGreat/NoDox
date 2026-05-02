import { FormEvent, useState } from "react";
import { fetchProfile } from "../api";
import { FetchProfileError } from "../types";

export default function LandingPage() {
  const [username, setUsername] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = username.trim().replace(/^@+/, "");
  const canSubmit = trimmed.length > 0 && !submitting;

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
          setError("private profile. NODOXX only audits public targets.");
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
    <main className="min-h-screen w-full flex items-center justify-center px-4 py-10">
      <section className="w-full max-w-xl border border-nodoxx-border bg-nodoxx-panel">
        {/* Title bar — mimics a terminal window chrome but flat. The brand
            lives here as a ranked-down identifier so the value proposition
            below can claim the visual hierarchy. */}
        <div className="flex items-center justify-between border-b border-nodoxx-border px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-nodoxx-muted">
          <span className="flex items-center gap-2">
            <span className="text-nodoxx-text font-bold tracking-[0.24em]">NODOXX</span>
            <span aria-hidden="true">//</span>
            <span>self-audit shell</span>
          </span>
          <span className="flex gap-1.5" aria-hidden="true">
            <span className="h-2 w-2 border border-nodoxx-muted/60" />
            <span className="h-2 w-2 border border-nodoxx-muted/60" />
            <span className="h-2 w-2 border border-nodoxx-muted/60" />
          </span>
        </div>

        <div className="px-8 py-10">
          <header className="mb-8">
            {/* The promise leads — what the tool *does for the user* claims
                top of the visual hierarchy, not the wordmark. Sans-serif for
                presence and readability; mono is reserved for code-shaped
                tokens (labels, stamps, the run_audit button). */}
            <h1
              className="font-sans font-semibold text-nodoxx-text leading-[1.05] text-3xl sm:text-[36px]"
            >
              See what strangers can find about you on Instagram.
              <span
                aria-hidden="true"
                className="ml-1 inline-block h-[0.7em] w-[0.4em] -translate-y-[0.05em] bg-nodoxx-text align-middle animate-caret-blink"
              />
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-nodoxx-muted">
              <span className="font-mono text-nodoxx-text">// </span>
              One-shot OSINT self-audit. Identity matches, geolocation signals,
              and web footprint &mdash; surfaced and scored.
            </p>
          </header>

          <form onSubmit={onSubmit} className="space-y-3">
            <label className="block">
              {/* Self-audit framing reinforced in the field label — the user
                  is meant to type *their own* handle, not someone else's. */}
              <span className="mb-1.5 block font-mono text-[10px] uppercase tracking-[0.2em] text-nodoxx-muted">
                [your.public.handle]
              </span>
              <div className="flex items-center border border-nodoxx-border bg-nodoxx-bg px-3 transition-colors focus-within:border-nodoxx-text">
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
                  placeholder="your_username"
                  disabled={submitting}
                  className="w-full bg-transparent py-3 pr-2 font-mono text-base text-nodoxx-text placeholder-nodoxx-dim outline-none disabled:opacity-60"
                />
              </div>
            </label>

            <button
              type="submit"
              disabled={!canSubmit}
              className="flex w-full items-center justify-center gap-3 border border-nodoxx-text bg-nodoxx-text py-3 px-4 font-mono text-sm font-semibold uppercase tracking-[0.2em] text-nodoxx-bg transition-[transform,colors] hover:bg-nodoxx-bg hover:text-nodoxx-text active:translate-y-px disabled:cursor-not-allowed disabled:border-nodoxx-border disabled:bg-transparent disabled:text-nodoxx-dim"
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
                className="border border-nodoxx-text/40 bg-white/[0.03] px-3 py-2 font-mono text-xs text-nodoxx-text"
                role="alert"
              >
                <span className="text-nodoxx-muted">err: </span>
                {error}
              </p>
            ) : null}
          </form>

          {/* Trust hooks elevated. Three short, scannable lines so the consent
              promise reads as part of the offer, not legal fine print. */}
          <ul className="mt-7 grid gap-1 font-mono text-[11px] leading-relaxed text-nodoxx-muted">
            <li><span className="text-nodoxx-text">[ok]</span> public data only &mdash; no login, no scraping behind the wall.</li>
            <li><span className="text-nodoxx-text">[ok]</span> single session, in-memory &mdash; nothing is persisted.</li>
            <li><span className="text-nodoxx-text">[ok]</span> intended for handles you own. don&apos;t use it on anyone else.</li>
          </ul>
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
