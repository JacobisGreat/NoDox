import { MeMediaResponse, OAuthStartResponse, SessionView } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      // Ignore JSON parse failures and keep default error.
    }
    throw new Error(detail);
  }

  return (await response.json()) as T;
}

export function getSession(): Promise<SessionView> {
  return request<SessionView>("/session");
}

export function startMetaOAuth(): Promise<OAuthStartResponse> {
  return request<OAuthStartResponse>("/auth/meta/start");
}

export function logout(): Promise<SessionView> {
  return request<SessionView>("/auth/logout", { method: "POST" });
}

export function fetchMeMedia(): Promise<MeMediaResponse> {
  return request<MeMediaResponse>("/me/media");
}
