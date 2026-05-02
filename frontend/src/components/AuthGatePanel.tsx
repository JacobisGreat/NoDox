import { SessionView } from "../types";

interface AuthGatePanelProps {
  session: SessionView | null;
  busy: boolean;
  callbackMessage: string | null;
  onConnect: () => void;
  onRefreshMedia: () => void;
  onReset: () => void;
}

function statusColor(session: SessionView | null): string {
  if (!session) return "bg-slate-500";
  if (session.gate_passed) return "bg-nodox-low";
  if (session.auth_status === "error") return "bg-nodox-high";
  if (session.auth_status === "auth_in_progress") return "bg-nodox-medium";
  return "bg-slate-500";
}

function statusLabel(session: SessionView | null): string {
  if (!session) return "Loading";
  if (session.gate_passed) return "Gate Passed";
  if (session.auth_status === "error") return "Gate Failed";
  if (session.auth_status === "auth_in_progress") return "Waiting For Callback";
  return "Not Connected";
}

export default function AuthGatePanel({
  session,
  busy,
  callbackMessage,
  onConnect,
  onRefreshMedia,
  onReset
}: AuthGatePanelProps) {
  return (
    <section className="rounded-lg border border-nodox-border bg-nodox-panel/60 p-5 shadow-neon">
      <div className="flex items-center justify-between">
        <h2 className="m-0 text-sm font-semibold uppercase tracking-normal text-nodox-cyan">
          OAuth Gate
        </h2>
        <div className="flex items-center gap-2 text-xs text-slate-300">
          <span className={`h-2.5 w-2.5 rounded-full ${statusColor(session)}`} />
          <span>{statusLabel(session)}</span>
        </div>
      </div>

      <p className="mb-4 mt-3 text-xs leading-5 text-slate-300">
        Pipelines remain locked until Instagram OAuth completes and backend verification of
        <span className="mx-1 text-nodox-cyan">GET /me/media</span>
        returns success.
      </p>

      {callbackMessage ? (
        <div className="mb-4 rounded border border-nodox-border bg-black/30 px-3 py-2 text-xs text-slate-200">
          {callbackMessage}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-2 text-xs text-slate-200 sm:grid-cols-2">
        <div className="rounded border border-nodox-border bg-black/20 p-3">
          <div className="text-slate-400">Instagram Username</div>
          <div className="mt-1 text-nodox-cyan">{session?.ig_user?.username ?? "N/A"}</div>
        </div>
        <div className="rounded border border-nodox-border bg-black/20 p-3">
          <div className="text-slate-400">Media Items Retrieved</div>
          <div className="mt-1">{session?.media_count ?? 0}</div>
        </div>
      </div>

      {session?.last_error ? (
        <div className="mt-3 rounded border border-nodox-high/60 bg-nodox-high/10 px-3 py-2 text-xs text-red-200">
          {session.last_error}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onConnect}
          disabled={busy}
          className="rounded border border-nodox-cyan bg-nodox-cyan/10 px-3 py-2 text-xs font-medium text-nodox-cyan transition hover:bg-nodox-cyan/20 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Connect Instagram
        </button>
        <button
          type="button"
          onClick={onRefreshMedia}
          disabled={busy}
          className="rounded border border-nodox-border bg-black/30 px-3 py-2 text-xs text-slate-200 transition hover:border-nodox-cyan hover:text-nodox-cyan disabled:cursor-not-allowed disabled:opacity-50"
        >
          Verify /me/media
        </button>
        <button
          type="button"
          onClick={onReset}
          disabled={busy}
          className="rounded border border-slate-600 bg-black/20 px-3 py-2 text-xs text-slate-300 transition hover:border-slate-400 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Reset Session
        </button>
      </div>
    </section>
  );
}
