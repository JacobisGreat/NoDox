import {
  FetchProfileError,
  FetchProfileErrorKind,
  FetchProfileResponse,
} from "./types";

const API_BASE = "/api";

async function readDetail(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (data && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // fall through
  }
  return response.statusText || "Request failed";
}

export async function fetchProfile(username: string): Promise<FetchProfileResponse> {
  const trimmed = username.trim().replace(/^@+/, "");
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/profile/fetch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: trimmed }),
    });
  } catch (err) {
    throw new FetchProfileError(
      "generic",
      err instanceof Error ? err.message : "Network error"
    );
  }

  if (response.ok) {
    return (await response.json()) as FetchProfileResponse;
  }

  let kind: FetchProfileErrorKind = "generic";
  if (response.status === 404) kind = "not_found";
  else if (response.status === 422) kind = "private";
  else if (response.status === 429) kind = "rate_limited";

  const detail = await readDetail(response);
  let retryAfter: number | null = null;
  if (kind === "rate_limited") {
    const header = response.headers.get("Retry-After");
    if (header) {
      const parsed = Number.parseInt(header, 10);
      if (Number.isFinite(parsed)) retryAfter = parsed;
    }
  }
  throw new FetchProfileError(kind, detail, retryAfter);
}

export async function startAudit(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/audit/${sessionId}/start`, {
    method: "POST",
  });
  if (response.status === 202) return;
  if (response.status === 409) return; // already started — treat as success
  const detail = await readDetail(response);
  throw new Error(detail);
}

export function streamUrl(sessionId: string): string {
  return `${API_BASE}/audit/${sessionId}/stream`;
}
