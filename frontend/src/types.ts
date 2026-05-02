export type AuthStatus = "unauthenticated" | "auth_in_progress" | "authenticated" | "error";

export interface SessionView {
  session_id: string;
  auth_status: AuthStatus;
  gate_passed: boolean;
  ig_user: {
    id?: string;
    username?: string;
    account_type?: string;
    media_count?: number;
  } | null;
  media_count: number;
  last_error: string | null;
}

export interface OAuthStartResponse {
  auth_url: string;
  session_id: string;
  gate_required: string;
}

export interface MeMediaResponse {
  gate_passed: boolean;
  media: Array<Record<string, unknown>>;
}
