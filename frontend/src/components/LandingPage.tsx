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
          setError("Profile not found. Check the username.");
        } else if (err.kind === "private") {
          setError(
            "This profile is private. NODOXX can only audit public profiles."
          );
        } else if (err.kind === "rate_limited") {
          setError("Instagram rate limit hit. Try again in a minute.");
        } else {
          setError("Something went wrong. Try again.");
        }
      } else {
        setError("Something went wrong. Try again.");
      }
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen w-full flex items-center justify-center px-4 py-10">
      <section
        className="w-full max-w-xl rounded-2xl border bg-nodoxx-panel/80 px-8 py-10 shadow-neon backdrop-blur-sm"
        style={{ borderColor: "rgba(31,63,99,0.35)" }}
      >
        <header className="mb-8 text-center">
          <h1
            className="font-mono font-bold text-nodoxx-accent tracking-[0.18em] text-5xl sm:text-6xl select-none"
            aria-label="NODOXX"
          >
            NODOXX
          </h1>
          <p className="mt-3 text-sm sm:text-base text-nodoxx-muted">
            See what attackers see. Consent-based Instagram OSINT self-audit.
          </p>
        </header>

        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="sr-only">Instagram username</span>
            <div
              className="flex items-center rounded-lg border bg-nodoxx-bg px-3 transition-colors focus-within:border-nodoxx-accent"
              style={{ borderColor: "rgba(31,63,99,0.45)" }}
            >
              <span className="select-none pr-2 text-nodoxx-muted font-mono text-base">@</span>
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
                className="w-full bg-transparent py-3 pr-2 font-mono text-base text-nodoxx-text placeholder-nodoxx-muted/60 outline-none disabled:opacity-60"
              />
            </div>
          </label>

          <button
            type="submit"
            disabled={!canSubmit}
            className="flex w-full items-center justify-center gap-3 rounded-lg bg-nodoxx-accent py-3 px-4 font-semibold text-nodoxx-bg transition-[filter,opacity] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <>
                <Spinner />
                <span className="font-mono text-sm">Fetching profile...</span>
              </>
            ) : (
              <span className="text-base tracking-wide">Run Audit</span>
            )}
          </button>

          {error ? (
            <p className="text-sm text-risk-high" role="alert">
              {error}
            </p>
          ) : null}
        </form>

        <footer className="mt-8 border-t pt-4 text-center text-xs text-nodoxx-muted/80"
          style={{ borderColor: "rgba(31,63,99,0.35)" }}
        >
          Audit only profiles you own. Public data, with your consent.
        </footer>
      </section>
    </main>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-nodoxx-bg/40 border-t-nodoxx-bg"
    />
  );
}
