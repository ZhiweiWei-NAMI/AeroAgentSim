import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { parseArgs } from 'node:util';
import { resolve, delimiter } from 'node:path';
import { mkdir } from 'node:fs/promises';
import { createWriteStream, writeFileSync } from 'node:fs';

const { values } = parseArgs({ options: {
  config: { type: 'string', default: process.env.RESCUE_CONFIG }, 'run-id': { type: 'string', default: process.env.RUN_ID }, 'uav-id': { type: 'string', default: process.env.UAV_ID },
  role: { type: 'string', default: process.env.ROLE ?? 'uav' }, 'data-dir': { type: 'string', default: process.env.DATA_DIR ?? '/data' },
  socket: { type: 'string', default: process.env.COMPANION_SOCKET ?? '/run/aas-uav/agent.sock' },
  mode: { type: 'string', default: process.env.AGENT_MODE ?? 'rule' },
  python: { type: 'string', default: process.env.AERORESCUE_PYTHON ?? 'python3' },
  help: { type: 'boolean' },
} });
if (values.help) {
  console.log('node launch.mjs --run-id ID --uav-id u1 --role uav --data-dir PATH --socket PATH [--config FILE] [--mode rule|model] [--python EXECUTABLE]');
  process.exit(0);
}
if (!values['run-id']) throw new Error('--run-id is required');
if (!values['uav-id']) {
  if (values.role === 'centre') values['uav-id'] = 'centre';
  else throw new Error('--uav-id or UAV_ID is required for a UAV');
}
if (!['rule', 'model'].includes(values.mode)) throw new Error('--mode must be rule or model');
if (!['uav', 'centre'].includes(values.role)) throw new Error('--role must be uav or centre');
// No endpoint/key discovery or fallback: model mode must be explicitly configured.
if (values.mode === 'model' && (!process.env.AAS_LLM_BASE_URL || !process.env.AAS_LLM_MODEL)) {
  throw new Error('Model mode requires AAS_LLM_BASE_URL and AAS_LLM_MODEL; credentials are required only by endpoints that use them; no rule fallback is performed');
}
const here = import.meta.dirname, appRoot = resolve(here, '..'), dataDir = resolve(values['data-dir']);
await mkdir(dataDir, { recursive: true });
await mkdir(resolve(dataDir, 'logs'), { recursive: true });
const eventLog = createWriteStream(resolve(dataDir, 'logs/harness-events.jsonl'), { flags: 'a' });
const errorLog = createWriteStream(resolve(dataDir, 'logs/harness-stderr.log'), { flags: 'a' });
writeFileSync(resolve(dataDir, 'logs/harness-mode.json'), JSON.stringify({
  run_id: values['run-id'], uav_id: values['uav-id'], role: values.role,
  decision_owner: 'DSH agent-loop', mode: values.mode,
  provider: values.mode === 'rule' ? 'rescue-rule' : 'rescue-model',
  rule_adapter_is_network_model: false,
  note: 'Mode/configuration is not evidence of a successful provider request. Inspect actual DSH session/request/assistant/tool events. Python model_calls measures only the execution worker.',
}, null, 2));
const workerArgs = ['--run-id', values['run-id'], '--uav-id', values['uav-id'], '--role', values.role, '--data-dir', dataDir, '--socket', values.socket];
if (values.config) workerArgs.push('--config', resolve(values.config));
const args = ['--profile', 'sdk-minimal'];
if (values.mode === 'model') args.push('--patch', resolve(here, 'model.patch.yml'));
args.push('--patch', resolve(here, 'rescue.patch.yml'));
const env = {
  ...process.env, DATA_DIR: dataDir, AGENT_MODE: values.mode,
  // ORT's API opt-out may run after native initialization. Disable 1DS before import.
  ORT_DISABLE_TELEMETRY: '1',
  DSH_HOME: resolve(dataDir, 'dsh'), AERORESCUE_PYTHON: values.python,
  AERORESCUE_WORKER_ARGS_JSON: JSON.stringify(workerArgs),
  AERORESCUE_SESSION_ID: `${values['run-id']}-${values['uav-id']}`,
  AAS_SKILLS_DIR: resolve(appRoot, 'skills'),
  PYTHONPATH: [resolve(appRoot, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter),
};
const child = spawn(resolve(here, 'node_modules/.bin/dsh'), args, { cwd: appRoot, env, stdio: ['pipe', 'pipe', 'pipe'] });
child.stderr.on('data', chunk => { errorLog.write(chunk); process.stderr.write(chunk); });
let seq = 0, stopping = false;
const requests = new Map();
const lines = createInterface({ input: child.stdout });
lines.on('line', line => {
  const message = JSON.parse(line);
  if (message.id !== undefined) {
    const pending = requests.get(message.id);
    if (pending) { requests.delete(message.id); message.error ? pending.reject(new Error(message.error.message)) : pending.resolve(message.result); }
  } else { eventLog.write(line + '\n'); process.stdout.write(line + '\n'); }
});
child.on('error', error => { for (const request of requests.values()) request.reject(error); process.exitCode = 1; });
child.on('exit', code => {
  for (const request of requests.values()) request.reject(new Error(`DSH exited (${code})`));
  if (!process.exitCode) process.exitCode = code ?? 1;
  eventLog.end(); errorLog.end();
});
function rpc(method, params = {}) {
  const id = ++seq;
  return new Promise((resolvePromise, reject) => {
    requests.set(id, { resolve: resolvePromise, reject });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
}
async function stop() {
  if (stopping) return;
  stopping = true;
  try { await rpc('shutdown'); } catch { child.kill('SIGTERM'); }
}
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
try {
  await rpc('initialize', { cwd: appRoot, provider: values.mode === 'rule' ? 'rescue-rule' : 'rescue-model', model: values.mode === 'rule' ? 'local-rescue-policy' : process.env.AAS_LLM_MODEL });
  // The rescue plugin creates/resumes its sole session through ctx.agents.
  // SDK initialize is only the host-readiness handshake, not a second owner.
} catch (error) {
  console.error(error.message);
  child.kill('SIGTERM');
  process.exitCode = 1;
}
