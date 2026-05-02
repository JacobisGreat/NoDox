import { useEffect, useRef, useState } from "react";
import { streamUrl } from "../api";

export interface SSEHandlers {
  onProfileLoaded: (data: unknown) => void;
  onPipelineStatus: (data: unknown) => void;
  onFinding: (data: unknown) => void;
  onCostUpdate: (data: unknown) => void;
  onAggregatorDone: (data: unknown) => void;
  onStreamDone: () => void;
  onError: (error: unknown) => void;
}

export interface SSEState {
  connected: boolean;
  close: () => void;
}

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;

export function useSSE(sessionId: string | null, handlers: SSEHandlers): SSEState {
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<number | null>(null);
  const closedRef = useRef(false);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const cleanup = () => {
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    setConnected(false);
  };

  useEffect(() => {
    if (!sessionId) return;
    closedRef.current = false;
    retryCountRef.current = 0;

    const parseAndDispatch = (
      raw: string,
      handler: (data: unknown) => void
    ) => {
      try {
        const data = raw ? JSON.parse(raw) : {};
        handler(data);
      } catch (err) {
        handlersRef.current.onError(err);
      }
    };

    const open = () => {
      if (closedRef.current) return;
      const source = new EventSource(streamUrl(sessionId));
      sourceRef.current = source;

      source.onopen = () => {
        retryCountRef.current = 0;
        setConnected(true);
      };

      source.addEventListener("profile_loaded", (e) =>
        parseAndDispatch((e as MessageEvent).data, handlersRef.current.onProfileLoaded)
      );
      source.addEventListener("pipeline_status", (e) =>
        parseAndDispatch((e as MessageEvent).data, handlersRef.current.onPipelineStatus)
      );
      source.addEventListener("finding", (e) =>
        parseAndDispatch((e as MessageEvent).data, handlersRef.current.onFinding)
      );
      source.addEventListener("cost_update", (e) =>
        parseAndDispatch((e as MessageEvent).data, handlersRef.current.onCostUpdate)
      );
      source.addEventListener("aggregator_done", (e) =>
        parseAndDispatch((e as MessageEvent).data, handlersRef.current.onAggregatorDone)
      );
      source.addEventListener("stream_done", () => {
        closedRef.current = true;
        handlersRef.current.onStreamDone();
        cleanup();
      });

      source.onerror = (err) => {
        if (closedRef.current) return;
        setConnected(false);
        source.close();
        sourceRef.current = null;
        if (retryCountRef.current >= MAX_RETRIES) {
          handlersRef.current.onError(err);
          return;
        }
        retryCountRef.current += 1;
        retryTimerRef.current = window.setTimeout(open, RETRY_DELAY_MS);
      };
    };

    open();

    return () => {
      closedRef.current = true;
      cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  return {
    connected,
    close: () => {
      closedRef.current = true;
      cleanup();
    },
  };
}
