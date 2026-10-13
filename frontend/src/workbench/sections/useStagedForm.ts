import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type SetStateAction } from 'react';
import { canonicalJson } from './operations';

/** The three-argument hook uses the panel's revision; standalone forms can supply it explicitly. */
export const StagedFormWorkspace = createContext<{ id: string; revision: number } | undefined>(undefined);
const prefix = 'aas.staged.';
const listeners = new Set<() => void>();
const pendingInMemory = new Map<string, Buffer<unknown>>();
const encodeKey = (value: string) => encodeURIComponent(value).replace(/\./g, '%2E');
const workspacePrefix = (id: string) => `${prefix}${encodeKey(id)}.`;
const formSuffix = (form: string) => `.${encodeKey(form)}`;
const memoryKeyFor = (id: string, form: string) => `${workspacePrefix(id)}pending${formSuffix(form)}`;
const storageKey = (id: string, revision: number, form: string) => `${workspacePrefix(id)}${revision}${formSuffix(form)}`;

function keysFor(id: string, form?: string): string[] {
  return Object.keys(sessionStorage).filter(key => key.startsWith(workspacePrefix(id)) && (form === undefined || key.endsWith(formSuffix(form))));
}
function notify() { listeners.forEach(listener => listener()); }
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }

function hasInputs(id?: string): boolean {
  const matches = (key: string) => id === undefined ? key.startsWith(prefix) : key.startsWith(workspacePrefix(id));
  if ([...pendingInMemory.keys()].some(matches)) return true;
  try { return Object.keys(sessionStorage).some(matches); }
  catch { return true; } // If storage cannot be read, warn before leaving; the form reports the error.
}
let guarding = false;
function keepUnloadGuard() {
  if (guarding) return;
  guarding = true;
  addEventListener('beforeunload', event => {
    // Check persisted records too: closing Configure must not remove this protection.
    if (hasInputs()) { event.preventDefault(); event.returnValue = ''; }
  });
}

export function useStagedFormStatus(workspaceId: string): boolean {
  return useSyncExternalStore(subscribe, () => hasInputs(workspaceId), () => false);
}

interface Buffer<T> { values: T; baseline: T; revision?: number; restored: boolean; error?: string }
function sameInputShape(value: unknown, initial: unknown): boolean {
  if (initial === null) return value === null;
  if (Array.isArray(initial)) return Array.isArray(value) && (initial.length === 0 || value.every(item => sameInputShape(item, initial[0])));
  if (typeof initial === 'object') return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.entries(initial).every(([key, field]) => key in value && sameInputShape((value as Record<string, unknown>)[key], field));
  return typeof value === typeof initial;
}
function restore<T>(id: string | undefined, form: string, revision: number | undefined, initial: T): Buffer<T> {
  const fresh = { values: initial, baseline: initial, revision, restored: false };
  if (id === undefined) return fresh;
  const memory = pendingInMemory.get(memoryKeyFor(id, form)) as Buffer<T> | undefined;
  if (memory) return { ...memory, restored: true };
  try {
    if (!Number.isSafeInteger(revision)) throw Error('The form has no workspace revision. / 表单缺少工作区修订号。');
    const records = keysFor(id, form).map(key => {
      const record = JSON.parse(sessionStorage.getItem(key)!);
      if (record.workspace !== id || record.form !== form || !Number.isSafeInteger(record.revision)
        || !('values' in record) || !('baseline' in record) || key !== storageKey(id, record.revision, form)
        || !sameInputShape(record.values, initial) || !sameInputShape(record.baseline, initial)) throw Error('Invalid unsaved form record. / 未保存的表单记录无效。');
      return record as { values: T; baseline: T; revision: number };
    }).sort((a, b) => b.revision - a.revision);
    if (!records.length) return fresh;
    return { ...records[0], restored: true };
  } catch (problem) { return { ...fresh, error: String(problem) }; }
}

export interface StagedForm<T> {
  values: T; setValues: (value: SetStateAction<T>) => void;
  dirty: boolean; restored: boolean; error?: string; baseRevision?: number;
  /** Discard resets the inputs; successful apply clears only this form's buffer. */
  discard: () => void; clear: (appliedValues?: T) => void;
}

/** Persist raw, unconfirmed inputs, never previews, request ids or an invented server draft.
 * A pending buffer keeps its revision base when another form applies. Reopening restores it
 * across revision changes, and a new review always uses the current server revision.
 */
export function useStagedForm<T>(workspaceId: string | undefined, formKey: string, initial: T, baseRevision?: number): StagedForm<T> {
  const context = useContext(StagedFormWorkspace);
  const revision = baseRevision ?? (context?.id === workspaceId ? context?.revision : undefined);
  const identity = canonicalJson([workspaceId, formKey]);
  const latest = useRef({ initial, revision, identity });
  latest.current = { initial, revision, identity };
  const [buffer, setBuffer] = useState(() => restore(workspaceId, formKey, revision, initial));
  const current = useRef(buffer);
  current.current = buffer;
  const seen = useRef(identity);
  const dirty = canonicalJson(buffer.values) !== canonicalJson(buffer.baseline);

  useEffect(keepUnloadGuard, []);
  useEffect(() => {
    if (seen.current !== identity) {
      seen.current = identity;
      const next = restore(workspaceId, formKey, revision, initial);
      current.current = next; setBuffer(next);
    } else if (!dirty && !buffer.restored && !buffer.error && (buffer.revision !== revision || canonicalJson(buffer.baseline) !== canonicalJson(initial))) {
      const next = { values: initial, baseline: initial, revision, restored: false };
      current.current = next; setBuffer(next);
    }
  }, [identity, revision, canonicalJson(initial), dirty, buffer.restored, buffer.error]);

  const setValues = useCallback((change: SetStateAction<T>) => {
    if (latest.current.identity !== canonicalJson([workspaceId, formKey])) return;
    const previous = current.current;
    const values = typeof change === 'function' ? (change as (value: T) => T)(previous.values) : change;
    const next = { ...previous, values };
    if (workspaceId !== undefined) {
      const memoryKey = memoryKeyFor(workspaceId, formKey);
      const changed = canonicalJson(values) !== canonicalJson(previous.baseline);
      try {
        if (!Number.isSafeInteger(previous.revision)) throw Error('The form has no workspace revision. / 表单缺少工作区修订号。');
        if (changed) sessionStorage.setItem(storageKey(workspaceId, previous.revision!, formKey), JSON.stringify({
          workspace: workspaceId, form: formKey, revision: previous.revision, values, baseline: previous.baseline,
        }));
        else { keysFor(workspaceId, formKey).forEach(key => sessionStorage.removeItem(key)); next.restored = false; }
        pendingInMemory.delete(memoryKey);
        next.error = undefined;
      } catch (problem) { next.error = String(problem); pendingInMemory.set(memoryKey, next); }
    }
    current.current = next; setBuffer(next); notify();
  }, [workspaceId, formKey]);

  const clear = useCallback((appliedValues?: T) => {
    if (latest.current.identity !== canonicalJson([workspaceId, formKey])) return;
    // Input entered while a request was running is a new buffer, not part of that apply.
    if (appliedValues !== undefined && canonicalJson(current.current.values) !== canonicalJson(appliedValues)) return;
    const next: Buffer<T> = { values: latest.current.initial, baseline: latest.current.initial, revision: latest.current.revision, restored: false };
    if (workspaceId !== undefined) {
      try {
        keysFor(workspaceId, formKey).forEach(key => sessionStorage.removeItem(key));
        pendingInMemory.delete(memoryKeyFor(workspaceId, formKey));
      } catch (problem) { next.error = String(problem); }
    }
    current.current = next; setBuffer(next); notify();
  }, [workspaceId, formKey]);
  return { values: buffer.values, setValues, dirty: dirty || buffer.restored, restored: buffer.restored,
    error: buffer.error, baseRevision: buffer.revision, discard: () => clear(), clear };
}
