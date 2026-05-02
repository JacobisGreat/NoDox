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

// Severity is encoded as brightness on the strict 3-gray scale:
//   #FFFFFF (high) | #A1A1A1 (medium) | #666666 (low)

export function confidenceColor(value: number): string {
  if (value > 0.7) return "#FFFFFF";
  if (value > 0.4) return "#A1A1A1";
  return "#666666";
}

export function exposureColor(score: number): string {
  if (score <= 30) return "#666666";
  if (score <= 60) return "#A1A1A1";
  return "#FFFFFF";
}

export function exposureLabel(score: number): "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" {
  if (score <= 30) return "LOW";
  if (score <= 60) return "MEDIUM";
  if (score <= 85) return "HIGH";
  return "CRITICAL";
}

export function exposureBlurb(score: number): string {
  const band = exposureLabel(score);
  if (band === "LOW") return "Light public footprint. A stranger would learn very little.";
  if (band === "MEDIUM") return "Moderate footprint. Some signals worth tightening.";
  if (band === "HIGH") return "Significant findings. A motivated stranger could pivot from here.";
  return "Severe footprint. Treat the remediation list as priority work.";
}

export function riskBorderColor(level: RiskLevel): string {
  if (level === "LOW") return "#666666";
  if (level === "MEDIUM") return "#A1A1A1";
  return "#FFFFFF";
}

export function riskTextClass(level: RiskLevel): string {
  if (level === "LOW") return "text-kali-label";
  if (level === "MEDIUM") return "text-kali-dim";
  return "text-kali-text";
}

// Badge classes — text + border only. No bg overlays per strict palette.
// Severity is reinforced by the ASCII prefix in FindingCard.
export function riskBgClass(level: RiskLevel): string {
  if (level === "LOW") return "text-kali-label border-kali-border";
  if (level === "MEDIUM") return "text-kali-dim border-kali-border";
  if (level === "HIGH") return "text-kali-text border-kali-border";
  return "text-kali-text border-kali-text";
}

// Pre-attentive ASCII severity token. Replaces the old 2/3/4/5px left-border
// encoding (1px-only rule).
export function severityPrefix(level: RiskLevel): string {
  if (level === "LOW") return "[!]";
  if (level === "MEDIUM") return "[!!]";
  if (level === "HIGH") return "[!!!]";
  return "[!!!!]";
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
