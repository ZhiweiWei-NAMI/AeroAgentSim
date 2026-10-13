import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../workbench/api';
import { useI18n } from '../i18n/I18nProvider';
import { compiledParts, type AssistantApi, type AssistantEvent, type Connection, type MessageContext, type PlanPreview, type PlanVersion, type Session } from './api';
import type { DraftChange } from './draftChanges';

const problemText = (problem: unknown) => problem instanceof ApiError ? problem.detail : String(problem);
/** Events that change what the session shows beyond the transcript. */
const REFRESHING = new Set(['plan', 'plan_validated', 'applied', 'turn_finished']);

export interface PendingMessage { text: string; context?: MessageContext }

/** One conversation: its transcript (long-polled), current plan and actions. */
export function useAssistantSession(api: AssistantApi, sessionId: string | undefined) {
  const { locale } = useI18n();
  const L = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const [session, setSession] = useState<Session>();
  const [events, setEvents] = useState<AssistantEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState<PendingMessage>();
  const last = useRef(0);
  const refreshing = useRef<Promise<void>>();

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    // Coalesce bursts (plan + validation + turn end arrive together).
    if (refreshing.current) return refreshing.current;
    refreshing.current = api.session(sessionId).then(value => { setSession(value); setBusy(value.busy); }).catch(problem => setError(problemText(problem))).finally(() => { refreshing.current = undefined; });
    return refreshing.current;
  }, [api, sessionId]);

  useEffect(() => {
    setSession(undefined); setEvents([]); setBusy(false); setError(undefined); setPending(undefined); last.current = 0;
    if (!sessionId) return;
    const abort = new AbortController();
    let stopped = false;
    void (async () => {
      try {
        const value = await api.session(sessionId, abort.signal);
        if (stopped) return;
        setSession(value); setEvents(value.events); setBusy(value.busy); last.current = value.last_seq;
      } catch (problem) { if (!abort.signal.aborted) setError(problemText(problem)); return; }
      let delay = 0;
      while (!stopped) {
        if (delay) await new Promise(resolve => setTimeout(resolve, delay));
        if (stopped) break;
        try {
          const batch = await api.events(sessionId, last.current, 20, abort.signal);
          if (stopped) break;
          delay = 0; setError(undefined);
          if (batch.events.length) {
            last.current = Math.max(last.current, batch.last_seq);
            setEvents(previous => {
              const known = new Set(previous.map(event => event.seq));
              return [...previous, ...batch.events.filter(event => !known.has(event.seq))];
            });
            if (batch.events.some(event => event.kind === 'user')) setPending(undefined);
            if (batch.events.some(event => REFRESHING.has(event.kind))) void refresh();
          }
          setBusy(batch.busy);
        } catch (problem) {
          if (abort.signal.aborted || stopped) break;
          setError(problemText(problem)); delay = Math.min(8000, (delay || 1000) * 2);
        }
      }
    })();
    return () => { stopped = true; abort.abort(); };
  }, [api, sessionId, refresh]);

  const send = useCallback(async (text: string, context?: MessageContext) => {
    if (!sessionId || session?.id !== sessionId || !text.trim()) {
      const problem = new Error(!text.trim() ? L('Enter a message before sending.', '请输入消息后再发送。') : L('The conversation is still connecting.', '对话仍在连接。'));
      setError(problem.message);
      throw problem;
    }
    setPending({ text, context }); setBusy(true); setError(undefined);
    try { await api.send(sessionId, text.trim(), context); }
    catch (problem) { setPending(undefined); setBusy(false); setError(problemText(problem)); throw problem; }
  }, [api, sessionId, session?.id, locale]);
  const cancel = useCallback(async () => { if (sessionId) { try { await api.cancel(sessionId); } catch (problem) { setError(problemText(problem)); } } }, [api, sessionId]);
  const choose = useCallback(async (version: number) => {
    if (!sessionId) return;
    try { const value = await api.choose(sessionId, version); setSession(value); } catch (problem) { setError(problemText(problem)); }
  }, [api, sessionId]);
  const apply = useCallback(async (version: number) => {
    if (!sessionId) throw Error('No conversation');
    const record = await api.apply(sessionId, version);
    await refresh();
    return record;
  }, [api, sessionId, refresh]);

  const raw = session?.plan;
  const plan: PlanVersion | undefined = useMemo(() => raw ? compiledParts(raw) : undefined, [raw]);
  const applied = useMemo(() => plan ? session?.applied.filter(row => row.plan_version === plan.version).at(-1) : undefined, [session, plan]);
  return { session, events, busy, error, pending, plan, applied, send, cancel, choose, apply, refresh };
}
export type AssistantSession = ReturnType<typeof useAssistantSession> & {
  planPreview?: PlanPreview & {version: number};
  draftState?: { changes?: DraftChange[]; error?: string; dirty: boolean; latestVersion?: number };
};

export function useConnection(api: AssistantApi) {
  const [connection, setConnection] = useState<Connection>(), [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    void api.connection(abort.signal).then(setConnection).catch(problem => { if (!abort.signal.aborted) setError(problemText(problem)); });
    return () => abort.abort();
  }, [api]);
  const save = useCallback(async (body: { base_url: string; model: string; api_key?: string; clear_key?: boolean }) => {
    setSaving(true); setError(undefined);
    try { const value = await api.connect(body); setConnection(value); return value; }
    catch (problem) { setError(problemText(problem)); throw problem; } finally { setSaving(false); }
  }, [api]);
  const test = useCallback(async () => {
    setSaving(true); setError(undefined);
    try { setConnection(await api.test()); } catch (problem) { setError(problemText(problem)); } finally { setSaving(false); }
  }, [api]);
  return { connection, error, saving, save, test };
}
