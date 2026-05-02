import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchMeMedia, getSession, logout, startMetaOAuth } from "./api/client";
import AuthGatePanel from "./components/AuthGatePanel";
import ExposurePanel from "./components/ExposurePanel";
import PipelineStatusPanel from "./components/PipelineStatusPanel";
import { SessionView } from "./types";

export default function App() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [busy, setBusy] = useState(false);
  const [callbackMessage, setCallbackMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshSession = useCallback(async () => {
    setError(null);
    try {
      const current = await getSession();
      setSession(current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load session.");
    }
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.pathname === "/oauth/callback") {
      const status = url.searchParams.get("status");
      const message = url.searchParams.get("message");
      if (status === "success") {
        setCallbackMessage("OAuth completed. /me/media verification passed.");
      } else {
        setCallbackMessage(message ?? "OAuth failed or verification did not pass.");
      }
      window.history.replaceState({}, "", "/");
    }
    void refreshSession();
  }, [refreshSession]);

  const gateStateLabel = useMemo(() => {
    if (!session) return "UNKNOWN";
    return session.gate_passed ? "UNLOCKED" : "LOCKED";
  }, [session]);

  async function connectInstagram() {
    setBusy(true);
    setError(null);
    try {
      const payload = await startMetaOAuth();
      window.location.assign(payload.auth_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start OAuth.");
      setBusy(false);
    }
  }

  async function verifyMediaGate() {
    setBusy(true);
    setError(null);
    try {
      const mediaResponse = await fetchMeMedia();
      setCallbackMessage(
        `Gate check successful. Retrieved ${mediaResponse.media.length} media records from /me/media.`
      );
      await refreshSession();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gate verification failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resetSession() {
    setBusy(true);
    setError(null);
    try {
      const next = await logout();
      setSession(next);
      setCallbackMessage("Session reset. OAuth gate locked.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto min-h-screen w-full max-w-7xl px-4 py-6 md:px-6 lg:px-8">
      <header className="mb-6 rounded-lg border border-nodox-border bg-black/30 px-5 py-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-nodox-cyan">NoDox</div>
            <h1 className="m-0 mt-1 text-xl font-semibold text-slate-100">
              Personal OSINT Self-Audit Console
            </h1>
          </div>
          <div className="rounded border border-nodox-border bg-black/40 px-3 py-2 text-xs text-slate-300">
            Pipeline Access: <span className="text-nodox-cyan">{gateStateLabel}</span>
          </div>
        </div>
      </header>

      {error ? (
        <div className="mb-6 rounded border border-nodox-high/70 bg-nodox-high/15 px-4 py-3 text-xs text-red-200">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.15fr_1fr]">
        <div className="space-y-6">
          <AuthGatePanel
            session={session}
            busy={busy}
            callbackMessage={callbackMessage}
            onConnect={connectInstagram}
            onRefreshMedia={verifyMediaGate}
            onReset={resetSession}
          />
          <ExposurePanel gatePassed={Boolean(session?.gate_passed)} />
        </div>

        <div>
          <PipelineStatusPanel gatePassed={Boolean(session?.gate_passed)} />
        </div>
      </div>
    </main>
  );
}

