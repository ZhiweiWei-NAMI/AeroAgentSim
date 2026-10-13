import { useCallback, useEffect, useRef, useState } from 'react';
import { RunsApi } from '../feeds/http';
import { exactNanos } from './operator-timing';
import { createOperatorSource } from './operator-source-client';
import type { OperatorSourceControl } from './operator-source';
export { exactNanos, operatorTiming } from './operator-timing';

export interface OperatorInput {
  /** Shared queue keeps event admission and source closure in order. */
  serialize<T>(action: () => Promise<T>): Promise<T>;
  error?: string;
}

/** The live session owns its source; scene rendering cannot stall production. */
export function useOperatorInput(api: RunsApi, runId: string | undefined, mode: 'live' | 'replay', followingLive = mode === 'live'): OperatorInput {
  // One queue per session: a stable identity lets the follow effect observe the
  // source created by the lifecycle effect, on mount and after every mode flip.
  const queueRef = useRef<{ tail: Promise<unknown>; source?: OperatorSourceControl }>();
  if (!queueRef.current) queueRef.current = { tail: Promise.resolve() };
  const queue = queueRef.current;
  // A history pause retains the producer. followingLive prevents new writes;
  // serialize waits for the last issued write before the real pause control.
  const allowed = useRef(followingLive); allowed.current = followingLive;
  const [error, setError] = useState<string>();
  const serialize = useCallback(<T,>(action: () => Promise<T>): Promise<T> => {
    const execute = async () => {
      const source = queue.source;
      await source?.hold();
      try { return await action(); } finally { source?.resume(); }
    };
    const result = queue.tail.then(execute, execute); queue.tail = result; return result;
  }, [queue]);
  useEffect(() => {
    setError(undefined);
    if (!runId || mode !== 'live') return;
    const receiptKey = `aeroagentsim.operator-watermark/${encodeURIComponent(api.base)}/${runId}`;
    let source: OperatorSourceControl;
    try {
      const saved = localStorage.getItem(receiptKey);
      source = createOperatorSource(api, runId, {
        following: allowed.current,
        acknowledged: saved === null ? undefined : exactNanos(saved, 'Acknowledged operator watermark').toString(),
        onReceipt: ns => localStorage.setItem(receiptKey, exactNanos(ns, 'Acknowledged operator watermark').toString()),
        onError: setError,
      });
      queue.source = source;
    } catch (problem) { setError(String(problem)); return; }
    return () => { if (queue.source === source) queue.source = undefined; source.dispose(); };
  }, [api, runId, mode, queue]);
  // Declared after the lifecycle effect so the follow pass observes the source
  // it created — on mount and on every later live/replay transition.
  useEffect(() => { queue.source?.setFollowing(mode === 'live' && followingLive); }, [queue, mode, followingLive]);
  return { serialize, error };
}
