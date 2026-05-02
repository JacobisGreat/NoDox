export interface Profile {
  username: string;
  full_name: string;
  biography: string;
  followers: number;
  followees: number;
  is_private: boolean;
  is_verified: boolean;
  external_url: string | null;
  profile_pic_url: string;
  post_count_total: number;
}

export type PipelineName = "identity" | "geolocation" | "web_footprint";

export type PipelineStatus =
  | "idle"
  | "running"
  | "complete"
  | "error"
  | "budget_exceeded";

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Finding {
  source: string;
  evidence_chain: string[];
  confidence: number;
  risk_level: RiskLevel;
  remediation: string;
  metadata: Record<string, unknown>;
}

export interface PipelineFinding {
  pipeline: PipelineName;
  finding: Finding;
}

export interface RemediationItem {
  priority: number;
  action: string;
  reason: string;
  risk_level: RiskLevel;
}

export interface AggregatorResult {
  exposure_score: number;
  summary: string;
  remediation_list: RemediationItem[];
}

export interface PipelineStatusEvent {
  pipeline: PipelineName;
  status: PipelineStatus;
  detail: string | null;
}

export interface ProfileLoadedEvent {
  profile: Profile;
  post_count: number;
}

export interface CostUpdateEvent {
  cost: number;
}

export interface FetchProfileResponse {
  session_id: string;
  profile: Profile;
  post_count: number;
}

export type FetchProfileErrorKind =
  | "not_found"
  | "private"
  | "rate_limited"
  | "validation"
  | "generic";

export class FetchProfileError extends Error {
  kind: FetchProfileErrorKind;
  retryAfter: number | null;

  constructor(kind: FetchProfileErrorKind, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "FetchProfileError";
    this.kind = kind;
    this.retryAfter = retryAfter;
  }
}

export type KnowledgeNodeKind =
  | "profile"
  | "pipeline"
  | "finding"
  | "location"
  | "platform"
  | "url"
  | "identity"
  | "remediation"
  | "summary";

export type KnowledgeLinkKind =
  | "owns"
  | "contains"
  | "suggests"
  | "references"
  | "located_at"
  | "matches"
  | "summarizes";

export interface KnowledgeNode {
  id: string;
  label: string;
  kind: KnowledgeNodeKind;
  pipeline?: PipelineName;
  risk?: RiskLevel;
  score?: number;
  detail?: string;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeLink {
  source: string;
  target: string;
  kind: KnowledgeLinkKind;
  weight?: number;
}

export interface KnowledgeGraphData {
  nodes: KnowledgeNode[];
  links: KnowledgeLink[];
}
