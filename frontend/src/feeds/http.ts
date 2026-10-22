import { parseLosslessJson } from './lossless-json';
import { asNetworkError, checkedFetch, isRetryableReadError, requestJson, TransportError } from './transport';
import { validateBusinessEvents, validateKpiValues } from '../newcomers/validation';
import type { FeedCommit, Instant, RunHeader, ViewerFeed } from '../contracts/viewer-feed';

export interface WaitingContext { stream_ids: string[]; at_ns: string }
export interface RunInfo { id: string; scenario: string; status: string; until_ns: string; error?: string; studio_workspace?: string; studio_revision?: number; workspace_name?: string; configuration_digest?: string; waiting?: WaitingContext; template_id?:string; template_title?:string; template_title_zh?:string; knobs?:Record<string,string|number|boolean>; algorithms?:Record<string,string>; kpis?:import('../newcomers/api').KpiValues; /** False while KPIs/Story are still aggregating after a terminal status; absent on older servers. */ results_final?: boolean }
/** One journaled ingress admission: survives a page refresh, unlike the in-memory POST receipt. */
export interface AdmissionReceipt {
  id: string; status: string; submitted_at?: Instant | string; requested_at?: Instant;
  target_cut?: number | null; kind?: string; detail?: string;
  execution_cut?: number; execution_status?: string; execution_result?: unknown;
  command_id?: string; idempotency_key?: string; target?: unknown;
  [key: string]: unknown;
}
export interface CommitPage { commits: FeedCommit[]; next: number; status: string; waiting?: WaitingContext }
type ObjectValue = Record<string, unknown>;
// Waiting on live operator watermark progress is a normal live-run state, not a failure.
export function waitingContext(value: unknown): WaitingContext | undefined {
  if (value === undefined) return undefined;
  const waiting = object(value);
  const ids = array(waiting.stream_ids);
  ids.forEach(string);
  if (!ids.length || ids.some(id => !(id as string).length)) throw Error('Input wait requires its actual streams');
  string(waiting.at_ns);
  if (!/^\d+$/.test(waiting.at_ns)) throw Error('Input wait requires its simulated boundary');
  return { stream_ids: ids as string[], at_ns: waiting.at_ns };
}
export function isTerminalStatus(status: string): boolean {
  return ['completed', 'stopped', 'faulted', 'interrupted', 'input_timeout'].includes(status);
}
function pageWaiting(page: ObjectValue): WaitingContext | undefined {
  if (page.waiting === undefined && ['waiting_for_input', 'input_timeout'].includes(String(page.status))) throw Error('Input wait metadata is missing');
  return waitingContext(page.waiting);
}
function object(value: unknown): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Feed contract: expected object');
  return value as ObjectValue;
}
function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw Error('Feed contract: expected array');
  return value;
}
function string(value: unknown): asserts value is string {
  if (typeof value !== 'string') throw Error('Feed contract: expected string');
}
function counter(value: unknown): asserts value is number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw Error('Feed contract: invalid integer counter');
}
function instant(value: unknown) {
  const at = object(value); string(at.ns); counter(at.microstep);
  if (!/^(0|[1-9][0-9]*)$/.test(at.ns)) throw Error('Feed contract: noncanonical nanoseconds');
}
function key(value: unknown) { const ref = object(value); string(ref.id); if(typeof ref.generation==='string') { if(!/^(0|[1-9][0-9]*)$/.test(ref.generation))throw Error('Invalid generation'); } else counter(ref.generation); }
function portable(value: unknown): void {
  if(typeof value==='number') {if(!Number.isFinite(value) || Number.isInteger(value) && !Number.isSafeInteger(value))throw Error('Unsafe untagged numeric value');return;}
  if(Array.isArray(value)){value.forEach(portable);return;}
  if(value && typeof value==='object') {
    const data=object(value);
    if(Object.keys(data).length===1 && '$integer' in data){string(data.$integer);if(!/^-?(0|[1-9][0-9]*)$/.test(data.$integer) || data.$integer==='-0')throw Error('Invalid decimal integer');return;}
    if(Object.keys(data).length===1 && '$number' in data){string(data.$number);if(!Number.isFinite(Number(data.$number)))throw Error('Invalid decimal number');return;}
    if(Object.keys(data).length===1 && '$record' in data){Object.values(object(data.$record)).forEach(portable);return;}
    Object.values(data).forEach(portable);
  }
}
function temporal(value: ObjectValue) {
  if(value.validTo!==undefined && value.validTo!==null)instant(value.validTo);
  if(value.available!==undefined)instant(value.available);
  if(value.version!==undefined){const version=object(value.version);counter(version.journalIndex);counter(version.itemOrdinal);}
  if(value.acquired!==undefined){const stamp=object(value.acquired);string(stamp.clockId);string(stamp.mappingId);string(stamp.numerator);string(stamp.denominator);if(!/^-?(0|[1-9][0-9]*)$/.test(stamp.numerator) || !/^[1-9][0-9]*$/.test(stamp.denominator))throw Error('Invalid source stamp');}
}
export function validateHeader(value: unknown): RunHeader {
  const header = object(value);
  if (header.contract !== 'aeroagentsim.viewer-feed/v1') throw Error('Unsupported viewer contract');
  string(header.runId); string(header.registryDigest); instant(header.start);
  if (header.epoch !== undefined) string(header.epoch);
  if (header.kernelRunId !== undefined) string(header.kernelRunId);
  if (header.behaviour !== undefined) {
    const behaviour = object(header.behaviour);
    array(behaviour.extensions).forEach(string);
    portable(behaviour);
  }
  if (header.end !== undefined) instant(header.end);
  for (const item of array(header.types)) {
    const type = object(item); string(type.typeId); string(type.displayName); array(type.ancestors).forEach(string);
    if (type.directory !== undefined) string(type.directory);
  }
  for (const item of array(header.fields)) {
    const field = object(item); string(field.fieldId); string(field.displayName); string(field.valueType);
    if (field.unit !== undefined && field.unit !== null) string(field.unit);
    if (field.frame !== undefined && field.frame !== null) string(field.frame);
  }
  for (const item of array(header.presentation)) {
    const binding = object(item); string(binding.typeId); string(binding.positionField);
    if (!['enu', 'ned', 'wgs84'].includes(String(binding.frame))) throw Error('Invalid presentation frame');
    if (!['marker', 'model', 'label'].includes(String(object(binding.visual).kind))) throw Error('Invalid presentation visual');
  }
  return header as unknown as RunHeader;
}
export function validateCommit(value: unknown): FeedCommit {
  const commit = object(value); counter(commit.commitIndex); instant(commit.at);
  for (const item of array(commit.created)) { key(item); string(object(item).typeId); }
  array(commit.removed).forEach(key);
  for (const item of array(commit.facts)) {
    const fact = object(item); key(fact.entity); string(fact.fieldId); string(fact.producer); instant(fact.validFrom);
    if (!('value' in fact)) throw Error('Fact lacks value');
    portable(fact.value);temporal(fact);
  }
  for (const item of array(commit.retracted)) { const fact = object(item); key(fact.entity); string(fact.fieldId);if(fact.validFrom!==undefined)instant(fact.validFrom);temporal(fact); }
  for (const item of array(commit.edges)) {
    const edge = object(item); string(edge.edgeId); string(edge.relationId); key(edge.source); key(edge.target);
    if (!['assert', 'close', 'cancel'].includes(String(edge.op))) throw Error('Invalid relation operation');
    if(edge.validFrom!==undefined && edge.validFrom!==null)instant(edge.validFrom);temporal(edge);
  }
  for (const item of array(commit.messages)) {
    const message = object(item); string(message.id); string(message.schemaId); string(message.source); instant(message.at);
    if (!['command', 'event'].includes(String(message.kind)) || !('payload' in message)) throw Error('Invalid typed message');
    portable(message.payload);if(message.subjects!==undefined)array(message.subjects).forEach(key);
  }
  for (const item of array(commit.receipts)) { const receipt = object(item); string(receipt.commandId); string(receipt.status);portable(receipt.result); }
  const roles = (value: unknown) => Object.values(object(value)).forEach(key);
  const extensionTemporal = (row: ObjectValue) => {
    instant(row.validFrom);
    const { acquired, ...metadata } = row;
    temporal(metadata); portable(row);
  };
  if (commit.predicateTruth !== undefined) for (const item of array(commit.predicateTruth)) {
    const row = object(item); string(row.contextId); string(row.predicateId); string(row.profile); roles(row.roles);
    if (!['known', 'required_input', 'invalid_input'].includes(String(row.status))) throw Error('Invalid predicate status');
    if (row.status === 'known' ? typeof row.value !== 'boolean' : row.value !== null) throw Error('Predicate status/value mismatch');
    const diagnostics = array(row.diagnostics);
    if (row.status !== 'known' && !diagnostics.length) throw Error('Unknown predicate lacks diagnostics');
    instant(row.evaluatedAt); const cut = object(row.readCut); counter(cut.index); instant(cut.at);
    if (!['assert', 'close'].includes(String(row.op))) throw Error('Invalid predicate interval operation');
    if (row.acquired !== undefined) for (const item of Object.values(object(row.acquired))) {
      const stamp = object(item); string(stamp.clockId); string(stamp.mappingId);
      if (stamp.numerator !== undefined) { string(stamp.numerator); if (!/^-?(0|[1-9][0-9]*)$/.test(stamp.numerator)) throw Error('Invalid acquisition numerator'); }
      if (stamp.denominator !== undefined) { string(stamp.denominator); if (!/^[1-9][0-9]*$/.test(stamp.denominator)) throw Error('Invalid acquisition denominator'); }
    }
    extensionTemporal(row);
  }
  if (commit.chainInstances !== undefined) for (const item of array(commit.chainInstances)) {
    const row = object(item); ['instanceId', 'templateId', 'bindingId', 'packageDigest', 'state'].forEach(name => string(row[name]));
    roles(row.roles); object(row.variables); object(row.children);
    if (!['created', 'transitioned', 'completed', 'failed', 'canceled'].includes(String(row.lifecycle))) throw Error('Invalid chain lifecycle');
    if (typeof row.revision === 'number') counter(row.revision);
    else { const revision = typeof row.revision === 'string' ? row.revision : object(row.revision).$integer; string(revision); if (!/^(0|[1-9][0-9]*)$/.test(revision)) throw Error('Invalid chain revision'); }
    if (row.op !== undefined && !['assert', 'close'].includes(String(row.op))) throw Error('Invalid chain interval operation');
    if (row.transitionId !== undefined && row.transitionId !== null) string(row.transitionId);
    extensionTemporal(row);
  }
  if(commit.events !== undefined)commit.events=validateBusinessEvents(commit.events);
  if(commit.kpis !== undefined)commit.kpis=validateKpiValues(commit.kpis);
  return commit as unknown as FeedCommit;
}

export class RunsApi {
  constructor(readonly base = window.location.origin) {}
  async request(path: string, options?: RequestInit): Promise<unknown> {
    return requestJson(this.base, path, options, parseLosslessJson);
  }
  async runs(signal?: AbortSignal, includeKpis = true): Promise<RunInfo[]> {
    const rows = array(await this.request(includeKpis ? '/v1/runs' : '/v1/runs?include_kpis=false', { signal }));
    for (const item of rows) { const row = object(item); string(row.id); string(row.scenario); string(row.status); string(row.until_ns); row.waiting = pageWaiting(row); if(row.kpis!==undefined)row.kpis=validateKpiValues(row.kpis); }
    return rows as RunInfo[];
  }
  async start(input: { scenario_path: string } | { scenario: unknown }): Promise<RunInfo> {
    return await this.request('/v1/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }) as RunInfo;
  }
  async startSource(source: string): Promise<RunInfo> {
    object(JSON.parse(source)); // Validate syntax/shape without rewriting numeric tokens.
    return await this.request('/v1/runs', { method:'POST', headers:{'Content-Type':'application/json'}, body:'{"scenario":'+source+'}' }) as RunInfo;
  }
  async control(id: string, action: 'pause' | 'resume' | 'stop'): Promise<void> {
    await this.request(`/v1/runs/${encodeURIComponent(id)}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  }
  /** Journaled admission and execution evidence, preserving instants and portable results. */
  async admissions(id: string, signal?: AbortSignal): Promise<AdmissionReceipt[]> {
    const rows = array(await this.request(`/v1/runs/${encodeURIComponent(id)}/admissions`, { signal }));
    for (const item of rows) {
      const row = object(item);
      string(row.id); string(row.status);
      if (row.submitted_at !== undefined && typeof row.submitted_at !== 'string') instant(row.submitted_at);
      if (row.requested_at !== undefined) instant(row.requested_at);
      if (row.target_cut !== undefined && row.target_cut !== null) counter(row.target_cut);
      if (row.execution_cut !== undefined) counter(row.execution_cut);
      for (const key of ['kind', 'detail', 'execution_status', 'command_id', 'idempotency_key']) if (row[key] !== undefined) string(row[key]);
      if (row.execution_result !== undefined) portable(row.execution_result);
    }
    return rows as AdmissionReceipt[];
  }
}

/** A new run is admitted before its worker commits the identity header. */
export async function awaitRunConfiguration(api: RunsApi, runId: string, signal?: AbortSignal): Promise<unknown> {
  const deadline = Date.now() + 30_000;
  while (true) {
    signal?.throwIfAborted();
    try { return await api.request(`/v1/studio/runs/${encodeURIComponent(runId)}/configuration`, {signal}); }
    catch (error) {
      if (!String(error).includes('HTTP 422') || !String(error).includes('run WAL header is not committed yet') || Date.now() >= deadline) throw error;
      await new Promise<void>((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(signal?.reason); };
        const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, 250);
        signal?.addEventListener('abort', abort, {once:true});
        if (signal?.aborted) abort();
      });
    }
  }
}

export class HttpViewerFeed implements ViewerFeed {
  private readonly path: string;
  constructor(private api: RunsApi, id: string, private mode: 'live' | 'replay' = 'replay', private status?: (status: string, waiting?: WaitingContext) => void, private transportError?: (error: string | undefined) => void) {
    this.path = `/v1/runs/${encodeURIComponent(id)}`;
  }
  async header(): Promise<RunHeader> { return validateHeader(await this.api.request(`${this.path}/header`)); }
  async subscribe(fromIndex: number, onCommit: (commit: FeedCommit) => void, signal?: AbortSignal): Promise<void> {
    counter(fromIndex);
    let cursor = Math.max(1, fromIndex);
    const deliver = (value: unknown) => {
      const commit = validateCommit(value);
      if (commit.commitIndex < cursor) return; // A reconnect may repeat an acknowledged prefix.
      if (commit.commitIndex !== cursor) throw Error(`Journal gap: expected ${cursor}, got ${commit.commitIndex}`);
      onCommit(commit); cursor = commit.commitIndex + 1;
    };
    const reconnectDelay = (attempt: number) => new Promise<void>(resolve => {
      const finish = () => { clearTimeout(timer); signal?.removeEventListener('abort', finish); resolve(); };
      const timer = setTimeout(finish, Math.min(5000, 250 * 2 ** Math.min(attempt, 5)));
      signal?.addEventListener('abort', finish, { once: true });
      if (signal?.aborted) finish();
    });
    let pageRetries = 0;
    while (!signal?.aborted) {
      let response: unknown;
      try {
        response = await this.api.request(`${this.path}/commits?from=${cursor}&limit=256`, { signal });
        if (pageRetries) this.transportError?.(undefined);
        pageRetries = 0;
      } catch (error) {
        if (signal?.aborted) return;
        if (!isRetryableReadError(error)) throw error;
        this.transportError?.(`Connection interrupted; reconnecting from journal ${cursor}: ${String(error)}`);
        await reconnectDelay(pageRetries++);
        continue;
      }
      const page = object(response);
      const commits = array(page.commits); counter(page.next); string(page.status);
      this.status?.(page.status, pageWaiting(page));
      commits.forEach(deliver);
      if (page.next !== cursor) throw Error('Feed pagination cursor disagrees with journal');
      if (commits.length === 0) {
        if (isTerminalStatus(page.status)) {counter(page.finalCursor);if(page.finalCursor!==cursor)throw Error('Terminal cursor disagrees with journal');return;}
        if(this.mode==='replay')return;
        break;
      }
    }
    // Reconnect only the read transport; simulation execution is never retried.
    let retries = 0;
    while (!signal?.aborted) {
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      try {
        const response = await checkedFetch(this.api.base, `${this.path}/stream?from=${cursor}`, { signal, headers: { 'Last-Event-ID': String(cursor - 1) } });
        if (!response.body) throw new TransportError(0, 'event stream has no readable body');
        this.transportError?.(undefined);
        reader = response.body.getReader();const decoder = new TextDecoder();
        let buffer = '', ended = false;
        while (!signal?.aborted) {
          let chunk: ReadableStreamReadResult<Uint8Array>;
          try {chunk = await reader.read();}catch(error){if(signal?.aborted)return;throw asNetworkError(error);}
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          let boundary: RegExpExecArray | null;
          while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
            const frame = buffer.slice(0, boundary.index); buffer = buffer.slice(boundary.index + boundary[0].length);
            let event = 'message'; const data: string[] = [];
            for (const line of frame.split(/\r?\n/)) {
              if (line.startsWith('event:')) event = line.slice(6).trim();
              if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
            }
            if (event === 'commit') { deliver(parseLosslessJson(data.join('\n'))); retries = 0; }
            if (event === 'status') { const status = object(parseLosslessJson(data.join('\n'))); string(status.status); this.status?.(status.status, pageWaiting(status)); }
            if (event === 'end') { const end = object(parseLosslessJson(data.join('\n'))); string(end.status);counter(end.finalCursor);if(end.finalCursor!==cursor)throw Error('Stream ended before final cursor');this.status?.(end.status, pageWaiting(end)); ended = true; return; }
          }
        }
        if (signal?.aborted || ended) return;
        throw new TransportError(0, 'Connection interrupted; reconnecting to the recorded journal');
      } catch(error) {
        if(signal?.aborted)return;
        // Fetch/reader failures are classified at their source. Raw errors here
        // belong to parsing, validation or consumers and must never reconnect.
        if(!(error instanceof TransportError) || !isRetryableReadError(error))throw error;
        this.transportError?.(error.kind === 'http'
          ? `SSE HTTP ${error.status}: ${error.detail}`
          : (error.detail || error.message));
      }
      finally {if(reader) {try {await reader.cancel();}catch { /* A failed read already retains its transport error. */ }reader.releaseLock();}}
      await reconnectDelay(retries++);
    }
  }
}
