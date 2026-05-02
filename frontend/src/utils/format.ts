import { PipelineName, RiskLevel } from "../types";

const PIPELINE_TITLES: Record<PipelineName, string> = {
  identity: "Identity Cross-Reference",
  geolocation: "Geolocation",
  web_footprint: "Web Footprint",
};

export function pipelineTitle(name: PipelineName): string {
  return PIPELINE_TITLES[name];
}

export function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "0";
  if (value < 1000) return String(value);
  if (value < 10_000) return `${(value / 1000).toFixed(1)}K`;
  if (value < 1_000_000) return `${Math.round(value / 1000)}K`;
  if (value < 10_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  return `${Math.round(value / 1_000_000)}M`;
}

export function formatCost(value: number): string {
  if (!Number.isFinite(value)) return "$0.0000";
  return `$${value.toFixed(4)}`;
}

export function formatConfidence(value: number): string {
  if (!Number.isFinite(value)) return "0.00";
  const clamped = Math.max(0, Math.min(1, value));
  return clamped.toFixed(2);
}

export function confidenceColor(value: number): string {
  if (value > 0.7) return "#22c55e";
  if (value > 0.4) return "#f97316";
  return "#ef4444";
}

export function exposureColor(score: number): string {
  if (score <= 30) return "#22c55e";
  if (score <= 60) return "#f97316";
  return "#ef4444";
}

export function riskBorderColor(level: RiskLevel): string {
  if (level === "LOW") return "#22c55e";
  if (level === "MEDIUM") return "#f97316";
  return "#ef4444";
}

export function riskTextClass(level: RiskLevel): string {
  if (level === "LOW") return "text-risk-low";
  if (level === "MEDIUM") return "text-risk-medium";
  return "text-risk-high";
}

export function riskBgClass(level: RiskLevel): string {
  if (level === "LOW") return "bg-risk-low/15 text-risk-low border-risk-low/40";
  if (level === "MEDIUM") return "bg-risk-medium/15 text-risk-medium border-risk-medium/40";
  return "bg-risk-high/15 text-risk-high border-risk-high/40";
}

const URL_REGEX = /\b(https?:\/\/[^\s)]+)\b/g;

export interface TextSegment {
  type: "text" | "url";
  value: string;
}

export function splitUrls(input: string): TextSegment[] {
  if (!input) return [{ type: "text", value: "" }];
  const segments: TextSegment[] = [];
  let lastIndex = 0;
  for (const match of input.matchAll(URL_REGEX)) {
    const start = match.index ?? 0;
    if (start > lastIndex) {
      segments.push({ type: "text", value: input.slice(lastIndex, start) });
    }
    segments.push({ type: "url", value: match[0] });
    lastIndex = start + match[0].length;
  }
  if (lastIndex < input.length) {
    segments.push({ type: "text", value: input.slice(lastIndex) });
  }
  return segments;
}
