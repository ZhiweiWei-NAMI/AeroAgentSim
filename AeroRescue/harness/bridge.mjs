import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { EventEmitter } from 'node:events';
import { readFileSync } from 'node:fs';
import { mkdir, writeFile, rename, readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

/** Existing per-UAV tool queue plus worker facts; deliberately not another RPC framework. */
export class ExecutionBridge extends EventEmitter {
  constructor({ dataDir, python, workerArgs }) {
    super();
    this.dataDir = dataDir;
    this.python = python;
    this.workerArgs = workerArgs;
    this.queue = join(dataDir, 'tasks', 'tool-requests');
    this.child = null;
  }
  context() {
    return JSON.parse(readFileSync(join(this.dataDir, 'harness-context.json'), 'utf8'));
  }
  async start() {
    await mkdir(this.queue, { recursive: true });
    this.child = spawn(this.python, ['-m', 'aerorescue.companion.runtime', '--execution-only', ...this.workerArgs], {
      // Keep the pre-initialization opt-out even when this plugin is hosted directly.
      stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, ORT_DISABLE_TELEMETRY: '1' },
    });
    this.child.stderr.on('data', chunk => process.stderr.write(chunk));
    const lines = createInterface({ input: this.child.stdout });
    lines.on('line', line => {
      try { this.emit('fact', JSON.parse(line)); }
      catch (error) { process.stderr.write(`AeroRescue worker output error: ${error.message}\n`); }
    });
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Execution worker did not report ready')), 30000);
      const ready = fact => { if (fact.event === 'ready') { clearTimeout(timeout); this.off('fact', ready); resolve(); } };
      this.on('fact', ready);
      this.child.once('error', error => { clearTimeout(timeout); reject(error); });
      this.child.once('exit', (code, signal) => {
        clearTimeout(timeout);
        const error = new Error(`Execution worker exited (${code ?? signal})`);
        reject(error);
        this.emit('failure', error);
      });
    });
  }
  async propose(signal) {
    signal?.throwIfAborted();
    return JSON.parse(await readFile(join(this.dataDir, 'rule-proposal.json'), 'utf8'));
  }
  async submit(command, request_id = randomUUID().replaceAll('-', '')) {
    const queued = join(this.queue, `${request_id}.queued.json`);
    const temp = `${queued}.tmp`;
    await writeFile(temp, JSON.stringify({ ...command, request_id }));
    await rename(temp, queued);
    return { status: 'pending', request_id, operation_id: request_id };
  }
  async result(requestId) {
    return JSON.parse(await readFile(join(this.queue, `${requestId}.result.json`), 'utf8'));
  }
  async recordedRequest(requestId) {
    if (!/^[a-zA-Z0-9]+$/.test(requestId)) return null;
    for (const suffix of ['running', 'queued']) {
      try { return JSON.parse(await readFile(join(this.queue, `${requestId}.${suffix}.json`), 'utf8')); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    return null;
  }
  async notified(requestId, sessionId) {
    try {
      const receipt = JSON.parse(await readFile(join(this.queue, `${requestId}.harness-notified.json`), 'utf8'));
      return receipt.session_id === sessionId;
    } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  }
  async markNotified(requestId, sessionId) {
    const target = join(this.queue, `${requestId}.harness-notified.json`);
    await writeFile(`${target}.tmp`, JSON.stringify({ request_id: requestId, session_id: sessionId }));
    await rename(`${target}.tmp`, target);
  }
  async recordedCompletions() {
    const files = await readdir(this.queue);
    const facts = [];
    for (const file of files.filter(name => name.endsWith('.result.json'))) {
      const request_id = file.slice(0, -'.result.json'.length);
      const request = await this.recordedRequest(request_id);
      if (!request) continue;
      const result = await this.result(request_id);
      facts.push({ event: ['failed', 'unavailable', 'rejected', 'canceled'].includes(result.status) ? 'tool.failed' : 'tool.completed',
        request_id, operation_id: request_id, result, task_id: request.task_id, task_revision: request.task_revision,
        recovered: true });
    }
    return facts;
  }
  async outstandingRequests() {
    const files = await readdir(this.queue);
    const ids = new Set(files.filter(name => /\.(queued|running)\.json$/.test(name)).map(name => name.replace(/\.(queued|running)\.json$/, '')));
    const requests = [];
    for (const id of ids) {
      if (files.includes(`${id}.result.json`)) continue;
      const request = await this.recordedRequest(id);
      if (request) requests.push(request);
    }
    return requests;
  }
  close() { this.child?.kill('SIGTERM'); }
}
