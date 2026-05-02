import {
  AggregatorResult,
  Finding,
  KnowledgeGraphData,
  KnowledgeLink,
  KnowledgeLinkKind,
  KnowledgeNode,
  KnowledgeNodeKind,
  PipelineName,
  Profile,
  RiskLevel,
} from "../types";

export const PROFILE_NODE_ID = "profile:self";
export const SUMMARY_NODE_ID = "summary:audit";

const PIPELINES: PipelineName[] = ["identity", "geolocation", "web_footprint"];

export function pipelineNodeId(name: PipelineName): string {
  return `pipeline:${name}`;
}

export const ANCHOR_IDS: ReadonlySet<string> = new Set([
  PROFILE_NODE_ID,
  SUMMARY_NODE_ID,
  pipelineNodeId("identity"),
  pipelineNodeId("geolocation"),
  pipelineNodeId("web_footprint"),
]);

interface BuildArgs {
  profile: Profile | null;
  findingsByPipeline: Record<PipelineName, Finding[]>;
  aggregator: AggregatorResult | null;
}

interface NodeBag {
  registry: Map<string, KnowledgeNode>;
  links: KnowledgeLink[];
  linkSeen: Set<string>;
}

function findingNodeId(pipeline: PipelineName, idx: number): string {
  return `finding:${pipeline}:${idx}`;
}

function remediationNodeId(idx: number): string {
  return `remediation:${idx}`;
}

function entityNodeId(kind: KnowledgeNodeKind, raw: string): string {
  return `${kind}:${normalizeKey(raw)}`;
}

function normalizeKey(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, "_");
}

function asString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function riskRank(level: RiskLevel | undefined): number {
  if (level === "CRITICAL") return 4;
  if (level === "HIGH") return 3;
  if (level === "MEDIUM") return 2;
  if (level === "LOW") return 1;
  return 0;
}

function ensureNode(bag: NodeBag, node: KnowledgeNode): KnowledgeNode {
  const existing = bag.registry.get(node.id);
  if (existing) {
    if (riskRank(node.risk) > riskRank(existing.risk)) existing.risk = node.risk;
    if (node.detail && !existing.detail) existing.detail = node.detail;
    if (typeof node.score === "number" && (existing.score ?? -1) < node.score) {
      existing.score = node.score;
    }
    if (node.metadata && !existing.metadata) existing.metadata = node.metadata;
    return existing;
  }
  bag.registry.set(node.id, node);
  return node;
}

function pushLink(
  bag: NodeBag,
  source: string,
  target: string,
  kind: KnowledgeLinkKind,
  weight = 1,
): void {
  if (source === target) return;
  if (!bag.registry.has(source) || !bag.registry.has(target)) return;
  const key = `${source}->${target}|${kind}`;
  if (bag.linkSeen.has(key)) return;
  bag.linkSeen.add(key);
  bag.links.push({ source, target, kind, weight });
}

function extractDomain(value: string): string | null {
  try {
    const u = new URL(value);
    return u.hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return null;
  }
}

function findFirstUrl(finding: Finding): string | null {
  for (const item of finding.evidence_chain) {
    const m = item.match(/https?:\/\/[^\s)]+/);
    if (m) return m[0];
  }
  const md = finding.metadata ?? {};
  for (const key of ["profile_url", "url", "source_url", "page_url", "post_url"]) {
    const v = asString(md[key as keyof typeof md]);
    if (v && /^https?:\/\//i.test(v)) return v;
  }
  return null;
}

function shortHostLabel(host: string): string {
  const parts = host.split(".");
  if (parts.length >= 2) return parts.slice(-2).join(".");
  return host;
}

const STOP_WORDS = new Set([
  "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "from",
  "by", "with", "your", "you", "this", "that", "these", "those", "is", "are",
  "be", "as", "it", "its", "into", "out", "off", "any", "all", "no", "not",
  "if", "then", "than", "so", "such", "do", "does", "did", "can", "will",
  "should", "would", "could", "make", "set", "use", "delete", "remove",
  "review", "post", "posts", "account", "accounts", "page", "pages",
]);

function tokens(text: string): Set<string> {
  const out = new Set<string>();
  if (!text) return out;
  const lower = text.toLowerCase();
  const matches = lower.match(/[a-z0-9]{4,}/g);
  if (!matches) return out;
  for (const m of matches) {
    if (STOP_WORDS.has(m)) continue;
    out.add(m);
  }
  return out;
}

function tokenOverlap(a: Set<string>, b: Set<string>): number {
  let n = 0;
  for (const t of a) if (b.has(t)) n += 1;
  return n;
}

function truncateLabel(text: string, max = 48): string {
  const cleaned = text.replace(/\s+/g, " ").trim();
  if (cleaned.length <= max) return cleaned;
  return `${cleaned.slice(0, max - 1)}…`;
}

function addProfileNode(bag: NodeBag, profile: Profile | null): void {
  const handle = profile?.username ? `@${profile.username}` : "this account";
  ensureNode(bag, {
    id: PROFILE_NODE_ID,
    label: handle,
    kind: "profile",
    detail:
      profile?.full_name || profile?.biography || "Audited Instagram profile.",
    metadata: profile ? { ...profile } : undefined,
  });
}

function addPipelineNodes(bag: NodeBag): void {
  const labels: Record<PipelineName, string> = {
    identity: "identity",
    geolocation: "geolocation",
    web_footprint: "web footprint",
  };
  for (const name of PIPELINES) {
    ensureNode(bag, {
      id: pipelineNodeId(name),
      label: labels[name],
      kind: "pipeline",
      pipeline: name,
      detail: pipelineDetail(name),
    });
    pushLink(bag, PROFILE_NODE_ID, pipelineNodeId(name), "owns");
  }
}

function pipelineDetail(name: PipelineName): string {
  if (name === "identity") return "Username matches across 1,001 platforms.";
  if (name === "geolocation") return "Location signals from posts.";
  return "Open-web mentions and footprint.";
}

function addSummaryNode(bag: NodeBag, aggregator: AggregatorResult | null): void {
  if (!aggregator) {
    ensureNode(bag, {
      id: SUMMARY_NODE_ID,
      label: "synthesis",
      kind: "summary",
      detail: "Final synthesis lands here once all pipelines finish.",
    });
    return;
  }
  ensureNode(bag, {
    id: SUMMARY_NODE_ID,
    label: `exposure ${aggregator.exposure_score}`,
    kind: "summary",
    score: aggregator.exposure_score,
    risk: scoreToRisk(aggregator.exposure_score),
    detail: aggregator.summary,
    metadata: { exposure_score: aggregator.exposure_score },
  });
  for (const name of PIPELINES) {
    pushLink(bag, SUMMARY_NODE_ID, pipelineNodeId(name), "summarizes");
  }
}

function scoreToRisk(score: number): RiskLevel {
  if (score >= 86) return "CRITICAL";
  if (score >= 61) return "HIGH";
  if (score >= 31) return "MEDIUM";
  return "LOW";
}

function addFindingsForPipeline(
  bag: NodeBag,
  pipeline: PipelineName,
  findings: Finding[],
): void {
  findings.forEach((finding, idx) => {
    const id = findingNodeId(pipeline, idx);
    ensureNode(bag, {
      id,
      label: truncateLabel(finding.source, 28),
      kind: "finding",
      pipeline,
      risk: finding.risk_level,
      score: finding.confidence,
      detail: finding.evidence_chain[0] ?? finding.remediation,
      metadata: { ...finding.metadata, _finding: finding },
    });
    pushLink(bag, pipelineNodeId(pipeline), id, "contains");

    if (pipeline === "identity") attachIdentityEntities(bag, id, finding);
    else if (pipeline === "geolocation") attachGeoEntities(bag, id, finding);
    else attachWebEntities(bag, id, finding);
  });
}

// Identity findings cluster around a single shared username node — that hub
// is the visual statement "this username is reused on N platforms." We
// intentionally drop display_name and per-finding URL nodes; they crowd the
// graph without adding signal.
function attachIdentityEntities(
  bag: NodeBag,
  findingId: string,
  finding: Finding,
): void {
  const platformLabel = finding.source;
  const platformId = entityNodeId("platform", platformLabel);
  ensureNode(bag, {
    id: platformId,
    label: truncateLabel(platformLabel, 20),
    kind: "platform",
    risk: finding.risk_level,
  });
  pushLink(bag, findingId, platformId, "references");

  const md = finding.metadata ?? {};
  const username =
    asString(md.username) ?? asString(md.handle) ?? asString(md.account);
  if (username) {
    const idNode = entityNodeId("identity", `username:${username}`);
    ensureNode(bag, {
      id: idNode,
      label: `@${username}`,
      kind: "identity",
      risk: finding.risk_level,
      detail: "Reused username across platforms.",
    });
    pushLink(bag, findingId, idNode, "matches", 1.4);
  }
}

function attachGeoEntities(
  bag: NodeBag,
  findingId: string,
  finding: Finding,
): void {
  const md = finding.metadata ?? {};
  const city = asString(md.city);
  const country = asString(md.country);
  const region = asString(md.region);
  const locationName = asString(md.location_name);
  const lat = asFiniteNumber(md.lat);
  const lon = asFiniteNumber(md.lon);

  let label: string | null = null;
  let key: string | null = null;
  if (city && country) {
    label = `${city}, ${country}`;
    key = `${city.toLowerCase()}|${country.toLowerCase()}`;
  } else if (locationName) {
    label = locationName;
    key = locationName.toLowerCase();
  } else if (region && country) {
    label = `${region}, ${country}`;
    key = `${region.toLowerCase()}|${country.toLowerCase()}`;
  } else if (country) {
    label = country;
    key = country.toLowerCase();
  } else if (lat !== null && lon !== null) {
    label = `${lat.toFixed(2)}, ${lon.toFixed(2)}`;
    key = `coord:${lat.toFixed(2)},${lon.toFixed(2)}`;
  }

  if (label && key) {
    const locId = entityNodeId("location", key);
    ensureNode(bag, {
      id: locId,
      label: truncateLabel(label, 28),
      kind: "location",
      risk: finding.risk_level,
      detail:
        lat !== null && lon !== null
          ? `Centroid ${lat.toFixed(3)}, ${lon.toFixed(3)}`
          : label,
      metadata: lat !== null && lon !== null ? { lat, lon } : undefined,
    });
    pushLink(bag, findingId, locId, "located_at", 1.5);
  }
}

// Web findings dedupe to a single domain node per host. Per-URL nodes were
// noisy because most surfaces are unique-per-finding.
function attachWebEntities(
  bag: NodeBag,
  findingId: string,
  finding: Finding,
): void {
  const md = finding.metadata ?? {};
  const explicitDomain = asString(md.domain);
  const url =
    asString(md.url) ?? asString(md.page_url) ?? findFirstUrl(finding);

  const host = explicitDomain
    ? explicitDomain.replace(/^www\./, "").toLowerCase()
    : url
      ? extractDomain(url)
      : null;

  if (host) {
    const urlId = entityNodeId("url", host);
    ensureNode(bag, {
      id: urlId,
      label: shortHostLabel(host),
      kind: "url",
      risk: finding.risk_level,
      detail: url ?? host,
    });
    pushLink(bag, findingId, urlId, "references", 1.2);
  }
}

// Remediation→finding cross-links are valuable but visually noisy. Cap them
// at the top 2 highest-overlap findings per remediation, with a floor of 2
// shared significant tokens to avoid accidental matches.
function addRemediation(
  bag: NodeBag,
  aggregator: AggregatorResult | null,
  findingTokenIndex: { id: string; tokens: Set<string> }[],
): void {
  if (!aggregator) return;
  aggregator.remediation_list.forEach((item, idx) => {
    const id = remediationNodeId(idx);
    ensureNode(bag, {
      id,
      label: `${item.priority}. ${truncateLabel(item.action, 28)}`,
      kind: "remediation",
      risk: item.risk_level,
      score: item.priority,
      detail: `${item.action}\n\n${item.reason}`,
      metadata: { ...item },
    });
    pushLink(bag, SUMMARY_NODE_ID, id, "suggests", 1.2);

    const actionTokens = tokens(`${item.action} ${item.reason}`);
    if (actionTokens.size === 0) return;
    const ranked = findingTokenIndex
      .map((entry) => ({ id: entry.id, score: tokenOverlap(entry.tokens, actionTokens) }))
      .filter((r) => r.score >= 2)
      .sort((a, b) => b.score - a.score)
      .slice(0, 2);
    for (const r of ranked) {
      pushLink(bag, id, r.id, "matches", 0.4);
    }
  });
}

function buildFindingTokenIndex(
  bag: NodeBag,
  findingsByPipeline: Record<PipelineName, Finding[]>,
): { id: string; tokens: Set<string> }[] {
  const out: { id: string; tokens: Set<string> }[] = [];
  for (const pipeline of PIPELINES) {
    findingsByPipeline[pipeline].forEach((finding, idx) => {
      const id = findingNodeId(pipeline, idx);
      if (!bag.registry.has(id)) return;
      const text = [
        finding.remediation,
        finding.source,
        ...finding.evidence_chain,
      ]
        .filter(Boolean)
        .join(" ");
      out.push({ id, tokens: tokens(text) });
    });
  }
  return out;
}

export function buildKnowledgeGraph({
  profile,
  findingsByPipeline,
  aggregator,
}: BuildArgs): KnowledgeGraphData {
  const bag: NodeBag = {
    registry: new Map(),
    links: [],
    linkSeen: new Set(),
  };

  addProfileNode(bag, profile);
  addPipelineNodes(bag);
  addSummaryNode(bag, aggregator);
  pushLink(bag, PROFILE_NODE_ID, SUMMARY_NODE_ID, "summarizes", 0.8);

  for (const pipeline of PIPELINES) {
    addFindingsForPipeline(bag, pipeline, findingsByPipeline[pipeline]);
  }

  const findingTokenIndex = buildFindingTokenIndex(bag, findingsByPipeline);
  addRemediation(bag, aggregator, findingTokenIndex);

  return {
    nodes: Array.from(bag.registry.values()),
    links: bag.links,
  };
}

export function nodeKindLabel(kind: KnowledgeNodeKind): string {
  switch (kind) {
    case "profile": return "profile";
    case "pipeline": return "pipeline";
    case "finding": return "finding";
    case "location": return "location";
    case "platform": return "platform";
    case "url": return "domain";
    case "identity": return "identity";
    case "remediation": return "remediation";
    case "summary": return "summary";
  }
}

export function linkKindLabel(kind: KnowledgeLinkKind): string {
  switch (kind) {
    case "owns": return "owns";
    case "contains": return "contains";
    case "suggests": return "suggests";
    case "references": return "references";
    case "located_at": return "located at";
    case "matches": return "matches";
    case "summarizes": return "summarizes";
  }
}
