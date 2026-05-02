import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { startAudit } from "../api";
import {
  AggregatorResult,
  Finding,
  PipelineFinding,
  PipelineName,
  PipelineStatus,
  PipelineStatusEvent,
  Profile,
} from "../types";
import { useSSE } from "./useSSE";

const PIPELINES: PipelineName[] = ["identity", "geolocation", "web_footprint"];

const initialStatuses = (): Record<PipelineName, PipelineStatusState> =>
  PIPELINES.reduce(
    (acc, name) => {
      acc[name] = { status: "idle", detail: null };
      return acc;
    },
    {} as Record<PipelineName, PipelineStatusState>
  );

export interface PipelineStatusState {
  status: PipelineStatus;
  detail: string | null;
}

export interface AuditState {
  profile: Profile | null;
  postCount: number;
  pipelineStatuses: Record<PipelineName, PipelineStatusState>;
  findingsByPipeline: Record<PipelineName, Finding[]>;
  totalFindings: number;
  cost: number;
  costTick: number;
  aggregator: AggregatorResult | null;
  streamDone: boolean;
  connected: boolean;
  startError: string | null;
  streamError: string | null;
}

export function useAudit(sessionId: string | null): AuditState {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [postCount, setPostCount] = useState(0);
  const [pipelineStatuses, setPipelineStatuses] = useState<
    Record<PipelineName, PipelineStatusState>
  >(initialStatuses());
  const [findingsByPipeline, setFindingsByPipeline] = useState<
    Record<PipelineName, Finding[]>
  >({ identity: [], geolocation: [], web_footprint: [] });
  const [cost, setCost] = useState(0);
  const [costTick, setCostTick] = useState(0);
  const [aggregator, setAggregator] = useState<AggregatorResult | null>(null);
  const [streamDone, setStreamDone] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const startedRef = useRef(false);

  const onProfileLoaded = useCallback((data: unknown) => {
    const payload = data as { profile?: Profile; post_count?: number };
    if (payload.profile) setProfile(payload.profile);
    if (typeof payload.post_count === "number") setPostCount(payload.post_count);
  }, []);

  const onPipelineStatus = useCallback((data: unknown) => {
    const payload = data as PipelineStatusEvent;
    if (!payload || !payload.pipeline) return;
    setPipelineStatuses((prev) => ({
      ...prev,
      [payload.pipeline]: {
        status: payload.status,
        detail: payload.detail ?? null,
      },
    }));
  }, []);

  const onFinding = useCallback((data: unknown) => {
    const payload = data as PipelineFinding;
    if (!payload || !payload.pipeline || !payload.finding) return;
    setFindingsByPipeline((prev) => ({
      ...prev,
      [payload.pipeline]: [...prev[payload.pipeline], payload.finding],
    }));
  }, []);

  const onCostUpdate = useCallback((data: unknown) => {
    const payload = data as { cost?: number };
    if (typeof payload.cost === "number") {
      setCost(payload.cost);
      setCostTick((t) => t + 1);
    }
  }, []);

  const onAggregatorDone = useCallback((data: unknown) => {
    const payload = data as AggregatorResult;
    if (!payload) return;
    setAggregator({
      exposure_score: payload.exposure_score,
      summary: payload.summary,
      remediation_list: Array.isArray(payload.remediation_list)
        ? [...payload.remediation_list].sort((a, b) => a.priority - b.priority)
        : [],
    });
  }, []);

  const onStreamDone = useCallback(() => {
    setStreamDone(true);
  }, []);

  const onError = useCallback((err: unknown) => {
    setStreamError(
      err instanceof Error ? err.message : "Connection to audit stream lost."
    );
  }, []);

  const handlers = useMemo(
    () => ({
      onProfileLoaded,
      onPipelineStatus,
      onFinding,
      onCostUpdate,
      onAggregatorDone,
      onStreamDone,
      onError,
    }),
    [
      onProfileLoaded,
      onPipelineStatus,
      onFinding,
      onCostUpdate,
      onAggregatorDone,
      onStreamDone,
      onError,
    ]
  );

  const { connected } = useSSE(sessionId, handlers);

  useEffect(() => {
    if (!sessionId || startedRef.current) return;
    startedRef.current = true;
    startAudit(sessionId).catch((err) => {
      setStartError(err instanceof Error ? err.message : "Failed to start audit.");
    });
  }, [sessionId]);

  const totalFindings =
    findingsByPipeline.identity.length +
    findingsByPipeline.geolocation.length +
    findingsByPipeline.web_footprint.length;

  return {
    profile,
    postCount,
    pipelineStatuses,
    findingsByPipeline,
    totalFindings,
    cost,
    costTick,
    aggregator,
    streamDone,
    connected,
    startError,
    streamError,
  };
}
