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

// Severity is encoded as brightness on a single grayscale axis:
//   bright white = high (alarming) | mid gray = medium | dim gray = low
// Same convention applies to risk levels, confidence, and exposure score.

export function confidenceColor(value: number): string {
  if (value > 0.7) return "#fafafa";
  if (value > 0.4) return "#a3a3a3";
  return "#525252";
}

export function exposureColor(score: number): string {
  if (score <= 30) return "#525252";
  if (score <= 60) return "#a3a3a3";
  return "#fafafa";
}

// Categorical anchor for the exposure score. A bare number ("73 / 100") forces
// the user to invent their own threshold; the label collapses that judgment
// into a single pre-attentive token.
export function exposureLabel(score: number): "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" {
  if (score <= 30) return "LOW";
  if (score <= 60) return "MEDIUM";
  if (score <= 85) return "HIGH";
  return "CRITICAL";
}

// One-line interpretation paired with each band. Calibrates expectations and
// takes the place of a tutorial.
export function exposureBlurb(score: number): string {
  const band = exposureLabel(score);
  if (band === "LOW") return "Light public footprint. A stranger would learn very little.";
  if (band === "MEDIUM") return "Moderate footprint. Some signals worth tightening.";
  if (band === "HIGH") return "Significant findings. A motivated stranger could pivot from here.";
  return "Severe footprint. Treat the remediation list as priority work.";
}

export function riskBorderColor(level: RiskLevel): string {
  if (level === "LOW") return "#525252";
  if (level === "MEDIUM") return "#a3a3a3";
  return "#fafafa";
}

export function riskTextClass(level: RiskLevel): string {
  if (level === "LOW") return "text-risk-low";
  if (level === "MEDIUM") return "text-risk-medium";
  return "text-risk-high";
}

export function riskBgClass(level: RiskLevel): string {
  if (level === "LOW") return "bg-white/[0.04] text-risk-low border-risk-low/40";
  if (level === "MEDIUM") return "bg-white/[0.06] text-risk-medium border-risk-medium/40";
  return "bg-white/10 text-risk-high border-risk-high/60";
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
