import type { FeedCommit, RunHeader, ViewerFeed } from '../contracts/viewer-feed';

export interface RunInfo { id: string; scenario: string; status: string; until_ns: string; error?: string }
export interface CommitPage { commits: FeedCommit[]; next: number; status: string }
type ObjectValue = Record<string, unknown>;
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
function key(value: unknown) { const ref = object(value); string(ref.id); counter(ref.generation); }
export function validateHeader(value: unknown): RunHeader {
  const header = object(value);
  if (header.contract !== 'aeroagentsim.viewer-feed/v1') throw Error('Unsupported viewer contract');
  string(header.runId); string(header.registryDigest); instant(header.start);
  if (header.end !== undefined) instant(header.end);
  for (const item of array(header.types)) { const type = object(item); string(type.typeId); string(type.displayName); array(type.ancestors).forEach(string); }
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
  }
  for (const item of array(commit.retracted)) { const fact = object(item); key(fact.entity); string(fact.fieldId); }
  for (const item of array(commit.edges)) {
    const edge = object(item); string(edge.edgeId); string(edge.relationId); key(edge.source); key(edge.target);
    if (!['assert', 'close'].includes(String(edge.op))) throw Error('Invalid relation operation');
  }
  for (const item of array(commit.messages)) {
    const message = object(item); string(message.id); string(message.schemaId); string(message.source); instant(message.at);
    if (!['command', 'event'].includes(String(message.kind)) || !('payload' in message)) throw Error('Invalid typed message');
  }
  for (const item of array(commit.receipts)) { const receipt = object(item); string(receipt.commandId); string(receipt.status); }
  return commit as unknown as FeedCommit;
}

export class RunsApi {
  constructor(readonly base = window.location.origin) {}
  async request(path: string, options?: RequestInit): Promise<unknown> {
    const response = await fetch(`${this.base}${path}`, options);
    if (!response.ok) throw Error(`HTTP ${response.status}: ${await response.text()}`);
    return response.json();
  }
  async runs(signal?: AbortSignal): Promise<RunInfo[]> {
    const rows = array(await this.request('/v1/runs', { signal }));
    for (const item of rows) { const row = object(item); string(row.id); string(row.scenario); string(row.status); string(row.until_ns); }
    return rows as RunInfo[];
  }
  async start(input: { scenario_path: string } | { scenario: unknown }): Promise<RunInfo> {
    return await this.request('/v1/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }) as RunInfo;
  }
  async control(id: string, action: 'pause' | 'resume' | 'stop'): Promise<void> {
    await this.request(`/v1/runs/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
  }
}

export class HttpViewerFeed implements ViewerFeed {
  private readonly path: string;
  constructor(private api: RunsApi, id: string, private mode: 'live' | 'replay' = 'replay', private status?: (status: string) => void) {
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
    while (!signal?.aborted) {
      const page = object(await this.api.request(`${this.path}/commits?from=${cursor}&limit=256`, { signal }));
      const commits = array(page.commits); counter(page.next); string(page.status);
      this.status?.(page.status);
      commits.forEach(deliver);
      if (page.next !== cursor) throw Error('Feed pagination cursor disagrees with journal');
      if (commits.length === 0) {
        if (this.mode === 'replay' || ['completed', 'stopped', 'faulted', 'interrupted'].includes(page.status)) return;
        break;
      }
    }
    // Reconnect only the read transport; simulation execution is never retried.
    let retries = 0;
    while (!signal?.aborted) {
      const response = await fetch(`${this.api.base}${this.path}/stream?from=${cursor}`, { signal, headers: { 'Last-Event-ID': String(cursor - 1) } });
      if (!response.ok || !response.body) throw Error(`SSE HTTP ${response.status}`);
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = '', ended = false;
      try {
        while (!signal?.aborted) {
          const chunk = await reader.read();
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
            if (event === 'commit') { deliver(JSON.parse(data.join('\n'))); retries = 0; }
            if (event === 'end') { const end = object(JSON.parse(data.join('\n'))); string(end.status); this.status?.(end.status); ended = true; return; }
          }
        }
      } finally { await reader.cancel(); }
      if (signal?.aborted || ended) return;
      if (++retries > 3) throw Error('SSE disconnected before end; reconnect to the recorded prefix');
      await new Promise<void>(resolve => {
        const finish = () => { clearTimeout(timer); signal?.removeEventListener('abort', finish); resolve(); };
        const timer = setTimeout(finish, retries * 250);
        signal?.addEventListener('abort', finish, { once: true });
      });
    }
  }
}
