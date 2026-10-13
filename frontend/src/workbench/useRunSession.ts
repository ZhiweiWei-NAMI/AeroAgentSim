import { useEffect, useMemo, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import { awaitRunConfiguration, HttpViewerFeed, RunsApi, type WaitingContext } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import { useOperatorInput, type OperatorInput } from './useOperatorInput';
import { TERMINAL } from './api';
import { useI18n } from '../i18n/I18nProvider';

export interface RunSession {
  store: TemporalFeedStore; clock: PlaybackClock;
  inputSource: OperatorInput;
  /** Server-reported run status (initialized, running, paused, completed, …). */
  status: string; waiting?: WaitingContext;
  /** The replay or terminal subscription has delivered its complete journal. */
  journalComplete: boolean;
  /** A requested photo/trace moment still being delivered by the real journal. */
  pendingCut?: number;
  /** Last engine-reported input boundary, retained when pausing or stopping there. */
  reportedBoundaryNs?: string;
  /** Exact journal cut being viewed; undefined means "the live head / clock position". */
  cut?: number;
  /** True while the view follows the live head of an active run. */
  followingLive: boolean;
  error?: string; transportError?: string;
  /** Each commit batch bumps this so views re-render. */
  revision: number;
  seekOffset(offset: number): void; seekCut(commitIndex: number): void;
  togglePlayback(): void; backToLive(): void; setSpeed(speed: number): void;
}

/**
 * One recorded or live run: the journal feed, its temporal store and the playback clock.
 * History and playback change only the viewed journal position. The live input
 * source and simulation controls remain independent of the viewing cursor.
 */
export function useRunSession(apiBase: string, runId: string | undefined, mode: 'live' | 'replay', initialCut?: number): RunSession | undefined {
  const { locale } = useI18n(), L = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const runs = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const [state, setState] = useState<{ store: TemporalFeedStore; clock: PlaybackClock }>();
  const [status, setStatus] = useState('loading'), [waiting, setWaiting] = useState<WaitingContext>();
  const [reportedBoundaryNs, setReportedBoundaryNs] = useState<string>();
  const [journalComplete, setJournalComplete] = useState(false);
  const [error, setError] = useState<string>(), [transportError, setTransportError] = useState<string>();
  const [seekError, setSeekError] = useState<string>();
  const [cut, setCut] = useState<number>(), cutRef = useRef<number>();
  const [pendingCut, setPendingCut] = useState<number>(), requestedCut = useRef<number>();
  const [revision, setRevision] = useState(0);
  const follow = useRef(mode === 'live' && initialCut === undefined);
  const statusRef = useRef('loading');
  const tick = () => setRevision(value => value + 1);

  useEffect(() => {
    setState(undefined); setError(undefined); setSeekError(undefined); setTransportError(undefined); setCut(undefined); cutRef.current = undefined;
    setJournalComplete(false);
    requestedCut.current = initialCut; setPendingCut(requestedCut.current);
    setStatus('loading'); statusRef.current = 'loading'; setWaiting(undefined); setReportedBoundaryNs(undefined); follow.current = mode === 'live' && requestedCut.current === undefined;
    if (!runId) return;
    const abort = new AbortController();
    let paint: number | undefined, activeStore: TemporalFeedStore | undefined;
    const feed = new HttpViewerFeed(runs, runId, mode, (value, context) => {
      if(abort.signal.aborted)return;
      setStatus(value); statusRef.current = value;
      setWaiting(['waiting_for_input', 'input_timeout'].includes(value) ? context : undefined);
      if (context) setReportedBoundaryNs(previous => previous !== undefined && BigInt(previous) > BigInt(context.at_ns) ? previous : context.at_ns);
      if (activeStore) activeStore.liveFollow = mode === 'live' && follow.current && !TERMINAL.has(value) && value !== 'paused';
    }, setTransportError);
    void feed.header().then(async header => {
      if (abort.signal.aborted) return;
      if (header.epoch === undefined || header.kernelRunId === undefined) {
        const configuration = await awaitRunConfiguration(runs, runId, abort.signal) as { service_run_id: string; kernel_run_id: string; epoch: string };
        if (configuration.service_run_id !== runId || typeof configuration.epoch !== 'string') throw Error('Run configuration identity disagrees with its feed');
        header.epoch = configuration.epoch; header.kernelRunId = configuration.kernel_run_id;
      }
      if (abort.signal.aborted) return;
      if (header.scene?.city) {
        header.scene.city.url = new URL(header.scene.city.url, apiBase + '/').href;
        if (header.scene.city.assetsBase) header.scene.city.assetsBase = new URL(header.scene.city.assetsBase, apiBase + '/').href;
      }
      const store = new TemporalFeedStore(header), clock = new PlaybackClock(header.start.ns, mode === 'live' ? header.start.ns : header.end?.ns ?? header.start.ns);
      activeStore = store; store.liveFollow = mode === 'live' && follow.current;
      setState({ store, clock });
      return feed.subscribe(0, commit => {
        if(abort.signal.aborted)return;
        store.ingest(commit);
        if (BigInt(commit.at.ns) > BigInt(clock.end)) clock.end = commit.at.ns;
        if (mode === 'live' && follow.current) clock.seek(clock.end);
        if (requestedCut.current === commit.commitIndex) {
          follow.current = false; store.liveFollow = false; clock.pause(); clock.seek(commit.at.ns); cutRef.current = commit.commitIndex; setCut(commit.commitIndex);
          requestedCut.current = undefined; setPendingCut(undefined);
        }
        // Many commits arrive per chunk: retain each, project once per frame.
        if (paint === undefined) paint = requestAnimationFrame(() => {
          paint = undefined;
          if (abort.signal.aborted) return;
          try { store.seek(clock.ns, cutRef.current); tick(); } catch (problem) { setError(String(problem)); abort.abort(); }
        });
      }, abort.signal).then(() => {
        if (abort.signal.aborted) return;
        setJournalComplete(true);
        if (requestedCut.current !== undefined) {
          setSeekError(L(`Journal cut ${requestedCut.current} is absent from the complete journal`, `完整日志中不存在记录位置 ${requestedCut.current}`));
          requestedCut.current = undefined; setPendingCut(undefined);
        }
      });
    }).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    return () => { abort.abort(); if (paint !== undefined) cancelAnimationFrame(paint); };
  }, [runs, runId, mode]);

  // Keep the source attached while paused so it can observe an explicit resume.
  // Only the real run lifecycle governs production; seeking never holds it.
  const sourceLive = mode === 'live' && !TERMINAL.has(status);
  const inputSource = useOperatorInput(runs, runId, sourceLive ? 'live' : 'replay');
  if (!state || !runId) return undefined;
  const { store, clock } = state;
  const liveAllowed = () => mode === 'live' && !TERMINAL.has(statusRef.current) && statusRef.current !== 'paused';
  const seekOffset = (offset: number) => {
    const commit = store.commits[offset];
    if (!commit) return;
    requestedCut.current = undefined; setPendingCut(undefined); setSeekError(undefined);
    follow.current = false; store.liveFollow = false; clock.pause(); clock.seek(commit.at.ns);
    cutRef.current = commit.commitIndex; setCut(commit.commitIndex); store.seek(clock.ns, commit.commitIndex); tick();
  };
  return {
    store, clock, inputSource, status, waiting, reportedBoundaryNs, journalComplete, pendingCut, cut, error: error ?? seekError, transportError, revision,
    followingLive: mode === 'live' && follow.current && cut === undefined,
    seekOffset,
    seekCut: index => {
      if (!Number.isSafeInteger(index) || index < 1) { setSeekError(L(`Invalid journal cut ${index}`, `记录位置 ${index} 无效`)); return; }
      const offset = store.commits.findIndex(commit => commit.commitIndex === index);
      if (offset >= 0) { seekOffset(offset); return; }
      if (journalComplete) { setSeekError(L(`Journal cut ${index} is absent from the complete journal`, `完整日志中不存在记录位置 ${index}`)); return; }
      requestedCut.current = index; setPendingCut(index); setSeekError(undefined);
      follow.current = false; store.liveFollow = false; clock.pause(); tick();
    },
    togglePlayback: () => { requestedCut.current = undefined; setPendingCut(undefined); setCut(undefined); cutRef.current = undefined; follow.current = false; store.liveFollow = false; if (clock.playing) clock.pause(); else { if (BigInt(clock.ns) >= BigInt(clock.end)) clock.seek(store.header.start.ns); clock.play(); } tick(); },
    backToLive: () => { requestedCut.current = undefined; setPendingCut(undefined); follow.current = mode === 'live'; store.liveFollow = liveAllowed(); setCut(undefined); cutRef.current = undefined; clock.pause(); clock.seek(clock.end); store.seek(clock.ns); tick(); },
    setSpeed: speed => { clock.setSpeed(speed); tick(); },
  };
}

/** Selection shared by every view: 3D, graph, lists, inspector and events. */
export function sameEntity(a?: EntityKey, b?: EntityKey) { return !!a && !!b && a.id === b.id && String(a.generation) === String(b.generation); }

/** The live clock uses the engine's reported waiting boundary; replay uses the viewed moment. */
export function runClockTime(mode: 'live' | 'replay', session: Pick<RunSession, 'store' | 'clock' | 'waiting' | 'reportedBoundaryNs'>) {
  if (mode === 'replay') return session.clock.ns;
  const head = session.store.commits.at(-1)?.at.ns ?? session.clock.ns;
  return [session.waiting?.at_ns, session.reportedBoundaryNs].reduce<string>((latest, boundary) => boundary !== undefined && BigInt(boundary) > BigInt(latest) ? boundary : latest, head);
}
