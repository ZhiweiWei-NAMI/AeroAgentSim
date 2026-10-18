import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { LlmAdapter, createUserMessage } from '@deepseek-ai/dsh-llm';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { loadSkills } from './skills.mjs';
import { ExecutionBridge } from './bridge.mjs';

export const name = 'aerorescue';
export const inject = ['llm', 'tools', 'agents', 'agentLoop', 'sessions', 'sessionPersistence', 'systemPrompt'];

class LocalRuleAdapter extends LlmAdapter {
  constructor(bridge) { super(); this.bridge = bridge; }
  async resolveModel(provider, model) {
    return { provider, id: model, name: model, context: { contextWindow: 1000000 } };
  }
  async *stream(options) {
    options.signal?.throwIfAborted();
    let proposal = await this.bridge.propose(options.signal);
    // Scoped comparison: keep normal inspection -> delivery stages, real bids,
    // cancellation and transport. Only omit replanning after resource withdrawal.
    if (process.env.AERORESCUE_ALLOCATION_POLICY === 'no-resource-replan') {
      const local = this.bridge.context();
      const incident = local.centre?.incidents?.[proposal?.request?.event_id];
      if (local.role === 'centre' && proposal?.control === 'centre-notice'
          && proposal.request.kind === 'offer' && incident?.replan_requested?.status === 'succeeded') {
        proposal = { ...proposal, control: 'centre-finish', request: {
          event_id: proposal.request.event_id, outcome: 'failed_execution',
        } };
      }
    }
    let block;
    if (proposal?.tool || proposal?.control || proposal?.wait_until_ns !== undefined) {
      const control = proposal.control ?? (proposal.wait_until_ns !== undefined ? 'wait-until' : undefined);
      const request = proposal.request ?? (control === 'wait-until' ? { sim_ns: proposal.wait_until_ns } : {});
      const args = { request_json: JSON.stringify(request), result_key: proposal.result_key ?? randomUUID(), continue_on_unavailable: Boolean(proposal.continue_on_unavailable) };
      if (control) args.action = control;
      block = { type: 'tool-call', id: randomUUID(), name: control ? 'rescue_control' : proposal.tool.replaceAll('-', '_'), arguments: JSON.stringify(args) };
    } else {
      block = { type: 'text', text: 'No action is currently proposed from local state; waiting for a real execution or radio event.' };
    }
    yield { type: 'block-start', index: 0, blockType: block.type };
    yield { type: 'block-end', index: 0, block };
    yield { type: 'usage', usage: { inputTokens: 0, outputTokens: 0 } };
    yield { type: 'finish', reason: { kind: block.type === 'tool-call' ? 'tool-calls' : 'stop' } };
  }
}

export async function apply(ctx) {
  const dataDir = resolve(process.env.DATA_DIR ?? '/data');
  const mode = process.env.AGENT_MODE ?? 'rule';
  if (!['rule', 'model'].includes(mode)) throw new Error('AGENT_MODE must be rule or model');
  const workerArgs = JSON.parse(process.env.AERORESCUE_WORKER_ARGS_JSON ?? '[]');
  if (!Array.isArray(workerArgs) || workerArgs.some(item => typeof item !== 'string')) throw new Error('Worker args must be a string array');
  const bridge = new ExecutionBridge({ dataDir, python: process.env.AERORESCUE_PYTHON ?? 'python3', workerArgs });
  const skills = await loadSkills(resolve(process.env.AAS_SKILLS_DIR ?? new URL('../skills/', import.meta.url).pathname));
  const owners = new Map(), pending = new Map(), early = new Map(), proposed = new Map(), signatures = new Map();
  const delivering = new Set();
  let agent = null, failed = null, closed = false, restoring = true;
  ctx.on('dispose', () => { closed = true; bridge.close(); });
  const wake = fact => {
    if (!agent || closed || ctx.agents.get(agent.id) !== agent) return;
    agent.followup(createUserMessage({ content: [{ type: 'text', text: JSON.stringify(fact) }], source: { kind: 'aerorescue-execution' } }));
  };
  bridge.on('failure', error => { if (!closed) { failed = error; wake({ event: 'execution.failed', reason: error.message }); } });
  const complete = async fact => {
    const id = fact.request_id ?? fact.operation_id;
    if (!id || delivering.has(id)) return;
    if (restoring) { early.set(id, fact); return; }
    let owner = owners.get(id);
    if (!owner) {
      if (!agent) { early.set(id, fact); return; }
      const request = await bridge.recordedRequest(id);
      if (!request) return;
      if (request.harness_session_id && request.harness_session_id !== agent.id) return;
      const current = bridge.context();
      if (fact.run_id && fact.run_id !== current.run_id || fact.uav_id && fact.uav_id !== current.uav_id) return;
      owner = agent;
    }
    if (delivering.has(id)) return;
    delivering.add(id);
    try {
    if (await bridge.notified(id, owner.id)) return;
    owners.delete(id);
    const signature = signatures.get(id);
    if (signature) { proposed.delete(signature); signatures.delete(id); }
    pending.get(owner.id)?.delete(id);
    if (ctx.agents.get(owner.id) !== owner) return;
    owner.followup(createUserMessage({ content: [{ type: 'text', text: JSON.stringify(fact) }], source: { kind: 'aerorescue-execution' } }));
    await ctx.sessions.flush(owner.session);
    // The event is now durably in DSH's inbox. This receipt suppresses repeated
    // notifications; recovery never submits or replays the underlying command.
    await bridge.markNotified(id, owner.id);
    } finally { delivering.delete(id); }
  };
  bridge.on('fact', fact => {
    if (fact.event === 'tool.completed' || fact.event === 'tool.failed') complete(fact).catch(error => { failed = error; wake({ event: 'execution.bridge-error', reason: error.message }); });
    else if (fact.event === 'context.changed') wake(fact);
  });
  await bridge.start();
  const proposalSignature = command => mode === 'rule' && command.result_key
    ? JSON.stringify([command.task_id, command.task_revision, command.tool, command.control, command.result_key, command.request]) : null;
  ctx.on('agent/created', async ({ agent: created }) => {
    if (agent && agent !== created) throw new Error('Each AeroRescue worker owns exactly one UAV/centre session');
    agent = created;
    for (const request of await bridge.outstandingRequests()) {
      if (request.harness_session_id && request.harness_session_id !== created.id) continue;
      owners.set(request.request_id, created);
      if (!pending.has(created.id)) pending.set(created.id, new Set());
      pending.get(created.id).add(request.request_id);
      const signature = proposalSignature(request);
      if (signature) {
        signatures.set(request.request_id, signature);
        proposed.set(signature, { status: 'pending', request_id: request.request_id, operation_id: request.request_id });
      }
    }
    restoring = false;
    // Creation listeners cannot await their own Agent turn. Recovery runs after
    // publication and uses only this UAV's existing request/result directory.
    setImmediate(async () => {
      try {
        for (const fact of await bridge.recordedCompletions()) await complete(fact);
        for (const fact of early.values()) await complete(fact);
        early.clear();
      } catch (error) { failed = error; wake({ event: 'execution.bridge-error', reason: error.message }); }
    });
  });
  ctx.on('agent/pre-step', async ({ agent: current, messages }, next) => {
    if (failed) throw failed;
    if (pending.get(current.id)?.size && messages.length === 0) return { kind: 'reject' };
    return next();
  });
  ctx.systemPrompt.section({ name: 'aerorescue-skills', order: 100, interpolate: false, text: skills.map(skill => skill.manual).join('\n\n') });
  ctx.systemPrompt.context({ name: 'aerorescue-local-state', order: 100, text: () => JSON.stringify(bridge.context()) });
  async function submit(args, exec, tool, control) {
    exec.signal.throwIfAborted();
    if (failed) throw failed;
    const request = JSON.parse(args.request_json);
    if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('request_json must encode an object');
    const snapshot = bridge.context();
    const task = snapshot.task ?? {};
    // Repeated context notifications must not enqueue the same outstanding
    // rule proposal twice. A distinct cancellation/control proposal remains free.
    const command = { ...(control ? { control } : { tool }), request,
      harness_session_id: exec.agent.id,
      result_key: args.result_key ?? exec.callId, continue_on_unavailable: Boolean(args.continue_on_unavailable),
      task_id: task.task_id ?? '', task_revision: task.task_revision ?? 0,
    };
    const signature = proposalSignature(command);
    if (signature && proposed.has(signature)) return proposed.get(signature);
    const request_id = randomUUID().replaceAll('-', '');
    const queued = { status: 'pending', request_id, operation_id: request_id };
    // Publish ownership before the queue file can become visible to Python.
    owners.set(queued.request_id, exec.agent);
    if (signature) { proposed.set(signature, queued); signatures.set(queued.request_id, signature); }
    if (!pending.has(exec.agent.id)) pending.set(exec.agent.id, new Set());
    pending.get(exec.agent.id).add(queued.request_id);
    try { await bridge.submit(command, request_id); }
    catch (error) {
      owners.delete(request_id); pending.get(exec.agent.id)?.delete(request_id);
      if (signature) { proposed.delete(signature); signatures.delete(request_id); }
      throw error;
    }
    const arrived = early.get(queued.request_id);
    if (arrived) { early.delete(queued.request_id); queueMicrotask(() => complete(arrived).catch(error => { failed = error; })); }
    return queued;
  }
  const extra = {
    result_key: { type: 'string', description: 'Optional workflow result slot; preserve the rule proposal value when supplied.' },
    continue_on_unavailable: { type: 'boolean', description: 'Preserve the proposal continuation behavior; does not turn unavailable into success.' },
  };
  const output = { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] };
  for (const skill of skills) {
    ctx.tools.register(defineTool({
      name: skill.toolName,
      description: `${skill.description} Input: ${skill.request_format.fields} Pending means accepted, not completed.`,
      parameters: { request_json: { type: 'string', required: true, description: `JSON object. ${skill.request_format.fields}` }, ...extra },
      output,
      execute: (args, exec) => submit(args, exec, skill.name),
    }));
  }
  ctx.tools.register(defineTool({
    name: 'rescue_control',
    description: 'Apply an explicit local mission/coordination action. accept-task uses a received message; apply-radio-control applies received cancellation/closure; withdraw-resources reports local inability; wait-until waits for simulation time. Centre-only: centre-solve computes a plan, centre-publish publishes its plan_id, centre-notice requests bids, centre-cancel cancels assignments, centre-finish records closure. Python enforces role and evidence requirements.',
    parameters: {
      action: { type: 'string', required: true, enum: ['accept-task', 'apply-radio-control', 'withdraw-resources', 'centre-solve', 'centre-publish', 'centre-notice', 'centre-cancel', 'centre-finish', 'wait-until'] },
      request_json: { type: 'string', required: true, description: 'JSON action arguments; see local context.control_inputs for exact current fields. wait-until: sim_ns; centre-solve: event_id, optional graph; centre-publish: plan_id; centre-notice: event_id, kind (offer|delivery|closure|clue-status), optional report_message_id, graph; centre-cancel: event_id, withdrawal_message_id; centre-finish: event_id, outcome, optional report_message_id. Use received evidence; never fabricate message IDs.' },
      ...extra,
    }, output,
    execute: (args, exec) => submit(args, exec, undefined, args.action),
  }));
  if (mode === 'rule') ctx.llm.registerAdapter(['rescue-rule'], new LocalRuleAdapter(bridge));
  // Use DSH's public session ownership, including its official resume API.
  // The SDK profile remains the supported host/transport; its prompt helper
  // only creates fresh sessions, so it is not used to own rescue sessions.
  const snapshot = bridge.context();
  const sessionId = process.env.AERORESCUE_SESSION_ID ?? `${snapshot.run_id}-${snapshot.uav_id}`;
  const agentOptions = { provider: mode === 'rule' ? 'rescue-rule' : 'rescue-model', model: mode === 'rule' ? 'local-rescue-policy' : process.env.AAS_LLM_MODEL };
  const stored = await ctx.sessionPersistence.stat(sessionId);
  const handle = stored
    ? await ctx.agents.resume({ resumeSessionId: sessionId, agentOptions })
    : await ctx.agents.create({ sessionId, agentOptions, meta: { cwd: resolve(new URL('../', import.meta.url).pathname) } });
  ctx.on('dispose', () => handle.dispose());
  handle.agent.followup(createUserMessage({ content: [{ type: 'text', text: JSON.stringify({ event: stored ? 'harness.resumed' : 'harness.started', run_id: snapshot.run_id, uav_id: snapshot.uav_id, instruction: 'Use current local state and installed tools. Never replay a command merely because a completion notification is recovered.' }) }], source: { kind: 'aerorescue-execution' } }));
}
