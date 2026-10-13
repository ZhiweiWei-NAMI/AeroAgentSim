import type { RunsApi } from '../feeds/http';
import type { OperatorSourceControl, OperatorSourceOptions } from './operator-source';

export type OperatorSourceRequest =
  | { kind: 'start'; base: string; runId: string; acknowledged?: string }
  | { kind: 'hold'; id: number }
  | { kind: 'resume' };
export type OperatorSourceReply =
  | { kind: 'held'; id: number }
  | { kind: 'receipt'; ns: string }
  | { kind: 'error'; error?: string }
  | { kind: 'fatal'; error: string };

export function createOperatorSource(api: RunsApi, runId: string, options: OperatorSourceOptions): OperatorSourceControl {
  const worker = new Worker(new URL('./operator-source.worker.ts', import.meta.url), { type: 'module', name: 'aeroagentsim-operator' });
  const pending = new Map<number, { resolve(): void; reject(error: Error): void }>();
  let sequence = 0, disposed = false, failed = false;
  const failure = (error: string) => {
    worker.terminate(); failed = true; options.onError(error);
    // The failed producer has been terminated; controls can now act on the
    // real run. No source acknowledgement is created by releasing these holds.
    pending.forEach(wait => wait.resolve()); pending.clear();
  };
  worker.addEventListener('error', event => { if (!disposed) failure(event.message); });
  worker.addEventListener('message', (event: MessageEvent<OperatorSourceReply>) => {
    if (disposed) return;
    const reply = event.data;
    if (reply.kind === 'held') {
      const wait = pending.get(reply.id);
      if (!wait) { failure('Unexpected operator source hold acknowledgement'); return; }
      wait.resolve(); pending.delete(reply.id);
    }
    else if (reply.kind === 'receipt') options.onReceipt(reply.ns);
    else if (reply.kind === 'error') options.onError(reply.error);
    else if (reply.kind === 'fatal') failure(reply.error);
    else failure('Unexpected operator source message');
  });
  const send = (message: OperatorSourceRequest) => worker.postMessage(message);
  send({ kind: 'start', base: api.base, runId, acknowledged: options.acknowledged });
  return {
    hold: () => {
      if (disposed) return Promise.reject(new Error('Operator source session was detached'));
      if (failed) return Promise.resolve();
      const id = ++sequence;
      const promise = new Promise<void>((resolve, reject) => pending.set(id, { resolve, reject }));
      send({ kind: 'hold', id }); return promise;
    },
    resume: () => { if (!disposed && !failed) send({ kind: 'resume' }); },
    dispose: () => {
      disposed = true; worker.terminate();
      pending.forEach(wait => wait.reject(new Error('Operator source session was detached'))); pending.clear();
    },
  };
}
