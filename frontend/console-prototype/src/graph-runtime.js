/** Deterministic local fixture adapter. Never executes native predicates or actuators. */
import { validateGraph, graphNodes, commandResourceDemand } from './graph-config.js';

const clone = value => JSON.parse(JSON.stringify(value));
const list = value => Array.isArray(value) ? value : [];
const isNs = value => typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value);
const unknown = () => ({ feasibility: 'UNKNOWN', permission: 'UNKNOWN', suitability: 'UNKNOWN' });
const indexOf = graph => Object.fromEntries(graphNodes(graph).map(({ id, node }) => [id, node]));

/** Resolve one scoped observation from selected fixture records at an exact instant. */
export function resolveGraphFact(graph, { entity_id, field_id, fact_ids, at_ns, source_id } = {}) {
  const base = { entity_id, field_id, at_ns, status: 'UNKNOWN', value: null, fact_id: null, evidence: null };
  if (!isNs(at_ns)) return { ...base, reason: 'invalid_time' };
  const candidates = list(graph?.facts).filter(fact => fact?.entity_id === entity_id && fact?.field_id === field_id && (!fact_ids || fact_ids.includes(fact.id)));
  if (!candidates.length) return { ...base, reason: 'missing_fact' };
  const contextual = candidates.filter(fact => !source_id || fact.source_id === source_id);
  if (!contextual.length) return { ...base, reason: 'source_mismatch' };
  const time = BigInt(at_ns);
  const wellTimed = contextual.filter(fact => isNs(fact.valid_time_ns) && isNs(fact.available_time_ns));
  const available = wellTimed.filter(fact => BigInt(fact.valid_time_ns) <= time && BigInt(fact.available_time_ns) <= time);
  if (!available.length) return { ...base, reason: 'not_yet_available' };
  available.sort((a, b) => BigInt(a.valid_time_ns) === BigInt(b.valid_time_ns) ? a.id.localeCompare(b.id) : BigInt(a.valid_time_ns) > BigInt(b.valid_time_ns) ? -1 : 1);
  const newest = available[0];
  if (available.filter(fact => fact.valid_time_ns === newest.valid_time_ns).length > 1) return { ...base, reason: 'ambiguous_observation' };
  const evidence = { fact_id: newest.id, source_id: newest.source_id, producer_module_id: newest.producer_module_id, valid_time_ns: newest.valid_time_ns, available_time_ns: newest.available_time_ns, valid_until_ns: newest.valid_until_ns, unit: newest.unit, frame: newest.frame, record_kind: newest.record_kind };
  if (newest.valid_until_ns !== null && (!isNs(newest.valid_until_ns) || time > BigInt(newest.valid_until_ns))) return { ...base, fact_id: newest.id, evidence, reason: 'stale_fact' };
  if (newest.value === null) return { ...base, fact_id: newest.id, evidence, reason: 'missing_value' };
  const field = list(graph?.fields).find(field => field.id === field_id);
  if (!field || newest.unit !== field.unit || newest.frame !== field.frame) return { ...base, fact_id: newest.id, evidence, reason: 'field_context_mismatch' };
  if (!list(graph?.sources).some(source => source.id === newest.source_id && source.kind === 'authored_fixture')) return { ...base, fact_id: newest.id, evidence, reason: 'source_mismatch' };
  return { ...base, status: 'AVAILABLE', value: clone(newest.value), fact_id: newest.id, evidence, reason: null };
}

/** Bounded admission is an explicitly fixture-only adapter, separate from Atlas. */
export function evaluateFixtureAdmission(graph, checkId, { fact_ids, at_ns, source_id } = {}) {
  const nodes = indexOf(graph), check = nodes[checkId], constraint = nodes[check?.constraint_id];
  const record = { id: `check-result-${checkId}-${at_ns}`, record_kind: 'fixture_admission_result', check_id: checkId, constraint_id: check?.constraint_id || null, at_ns, verdict: 'UNKNOWN', evidence: [], reason: 'unresolved_check', scope: 'fixture_only', actual_assessments: unknown() };
  if (!check || !constraint || !constraint.condition) return record;
  const observation = resolveGraphFact(graph, { entity_id: check.entity_id, field_id: check.field_id, fact_ids, at_ns, source_id });
  record.observation = observation;
  if (observation.evidence) record.evidence.push(observation.evidence);
  if (observation.status !== 'AVAILABLE') return { ...record, reason: observation.reason };
  const { operator, expected, expected_unit } = constraint.condition;
  if (expected_unit !== observation.evidence.unit) return { ...record, reason: 'threshold_unit_mismatch' };
  let permitted;
  if (operator === 'boolean_equals' && typeof observation.value === 'boolean' && typeof expected === 'boolean') permitted = observation.value === expected;
  else if (operator === 'number_lte' && Number.isFinite(observation.value) && Number.isFinite(expected)) permitted = observation.value <= expected;
  else if (operator === 'number_gte' && Number.isFinite(observation.value) && Number.isFinite(expected)) permitted = observation.value >= expected;
  else return { ...record, reason: 'unsupported_check_contract' };
  return { ...record, verdict: permitted ? 'PERMIT' : 'BLOCK', reason: 'bounded_fixture_condition', operator, expected, observed: clone(observation.value) };
}

/** Resolve the authored loop and run only bounded fixture admission. Immutable input. */
export function executeGraphFixture(graph, scenarioId, options = {}) {
  const validation = validateGraph(graph, options.config);
  const base = { schema_version: 'aeroagentsim.graph-fixture-result/v1', scenario_id: scenarioId, status: 'blocked', validation, readiness: { declared: true, static_checked: validation.valid, fixture_executed: false, real_connected: false }, facts: [], decisions: [], events: [], commands: [], behaviors: [], resource_usage: [], bindings: [], check_results: [], feedback: [], updated_facts: [], predicate_results: [], predicate_truth: 'UNKNOWN', assessments: unknown(), trace: [], boundary: 'Local fixture graph resolution and bounded fixture admission only. Accepted authored dispatch is not actuator execution, command effect, legal permission, or native predicate truth.' };
  if (!validation.valid) return { ...base, error: 'invalid_graph' };
  const nodes = indexOf(graph), scenario = nodes[scenarioId];
  if (!list(graph.scenarios).some(item => item.id === scenarioId)) return { ...base, error: 'unknown_scenario' };
  const at = options.at_ns ?? scenario.observed_at_ns;
  if (!isNs(at)) return { ...base, error: 'invalid_time' };
  const timeline = [...scenario.admission_timeline].sort((a, b) => BigInt(a.at_ns) < BigInt(b.at_ns) ? -1 : BigInt(a.at_ns) > BigInt(b.at_ns) ? 1 : 0).filter(step => BigInt(step.at_ns) <= BigInt(at));
  const startAt = timeline[0]?.at_ns || at;
  const result = { ...base, status: 'fixture_executed', stages: { schema: 'static_checked', facts: 'fixture_resolved', strategy: 'authored_decision_replay', command: 'composition_resolved', admission: scenario.admission_timeline.length ? 'fixture_checked' : 'not_executed', modules: 'not_executed', predicates: 'not_executed', actuators: 'not_executed' }, at_ns: at, label: scenario.label, readiness: { ...base.readiness, fixture_executed: true } };
  const trace = (kind, nodeId, message, atNs = startAt, evidenceIds = []) => result.trace.push({ step: result.trace.length, kind, node_id: nodeId, message, at_ns: atNs, evidence_ids: [...evidenceIds], scope: 'fixture_only' });
  const scopes = new Map();
  for (const id of scenario.fact_ids) { const fact = nodes[id]; scopes.set(`${fact.entity_id}.${fact.field_id}`, fact); }
  result.facts = [...scopes.values()].map(fact => resolveGraphFact(graph, { entity_id: fact.entity_id, field_id: fact.field_id, fact_ids: scenario.fact_ids, at_ns: at, source_id: options.source_id }));
  result.lease_requests = list(scenario.lease_request_ids).map(id => clone(nodes[id]));
  result.lease_approvals = list(scenario.lease_approval_ids).map(id => ({ ...clone(nodes[id]), actual_authority_verified: false }));
  result.space_time_allocations = list(scenario.allocation_ids).map(id => ({ ...clone(nodes[id]), actual_reservation: false }));
  result.delivery_receipts = list(scenario.delivery_receipt_ids).map(id => ({ ...clone(nodes[id]), actual_delivery_verified: false, task_success: 'UNKNOWN' }));
  trace('fixture_started', scenario.id, 'Resolve the same authored graph snapshot; all backend readiness remains disconnected.');
  const usedStrategies = new Set();
  for (const id of scenario.decision_ids) {
    const decision = nodes[id], strategy = nodes[decision.strategy_id], agent = nodes[decision.agent_id];
    const inputs = strategy.observable_fact_ids.map(factId => nodes[factId]).map(fact => resolveGraphFact(graph, { entity_id: fact.entity_id, field_id: fact.field_id, fact_ids: scenario.fact_ids, at_ns: startAt, source_id: options.source_id }));
    usedStrategies.add(strategy.id);
    trace('strategy_called', strategy.id, `${agent.id} binds observable facts, requests, soft objectives and hard constraints. This selects an authored fixture decision.`, startAt, inputs.map(input => input.fact_id).filter(Boolean));
    result.decisions.push({ ...clone(decision), record_kind: 'fixture_decision_resolution', authored_assessments: clone(decision.assessments), assessments: unknown(), input_observations: inputs, decision_time_ns: startAt, binding_status: 'contract_resolved', decision_selection: 'authored_decision_replay', actual_decision_engine: 'not_connected' });
    trace('decision_resolved', decision.id, decision.rationale, startAt, decision.command_ids);
  }
  const activeBehaviors = new Map();
  for (const id of scenario.command_ids) {
    const command = nodes[id];
    const output = { ...clone(command), record_kind: 'fixture_command_dispatch', acceptance: 'authored_fixture_dispatch', effect: 'not_executed', native_actuator_connected: false, actual_assessments: unknown() };
    result.commands.push(output);
    trace('command_composed', id, `${command.composition.mode} composition resolves ${command.composition.behavior_ids.length} behavior(s); no actuator is called.`, startAt, command.composition.behavior_ids);
    for (const [order, behaviorId] of command.composition.behavior_ids.entries()) {
      const behavior = nodes[behaviorId], condition = command.composition.conditions.find(item => item.behavior_id === behaviorId);
      const state = { ...clone(behavior), record_kind: 'fixture_behavior_plan', command_id: id, composition_mode: command.composition.mode, order, status: condition ? 'waiting_unknown_condition' : 'planned_unverified', effect: 'not_executed', module_execution: 'fixture_binding_only', condition_result: condition ? 'UNKNOWN' : null, admission: { verdict: 'UNKNOWN', constraint_results: [], unchecked_constraint_ids: [...behavior.constraint_ids] } };
      state.prerequisites = resolveBehaviorPrerequisites(graph, behaviorId, startAt);
      if (state.prerequisites.checks.length && !state.prerequisites.allowed_in_fixture) state.status = state.prerequisites.verdict === 'BLOCK' ? 'fixture_prerequisite_blocked' : 'fixture_prerequisite_unknown';
      activeBehaviors.set(behaviorId, state);
      result.behaviors.push(state);
      trace('behavior_bound', behaviorId, condition ? 'Conditional dependency remains UNKNOWN; behavior is not admitted.' : 'Resolved capability, modules, resources, constraints and completion dependencies.', startAt, [...behavior.module_ids, ...behavior.constraint_ids]);
    }
  }
  const bindingIds = new Set([...usedStrategies, ...result.commands.map(command => command.id), ...result.behaviors.flatMap(behavior => behavior.module_ids)]);
  result.bindings = [...bindingIds].map(id => ({ node_id: id, ...clone(nodes[id].binding), status: 'contract_resolved', real_connected: false, execution: 'not_executed' }));
  const usage = new Map();
  for (const taskId of scenario.task_ids) for (const claim of nodes[taskId].resource_claims) {
    const current = usage.get(claim.resource_id) || { resource_id: claim.resource_id, label: nodes[claim.resource_id].label, amount: 0, capacity: nodes[claim.resource_id].capacity, unit: claim.unit, task_ids: [], status: 'declared_allocation', actual_reservation: false };
    current.amount += claim.amount; current.task_ids.push(taskId); usage.set(claim.resource_id, current);
  }
  result.resource_usage = [...usage.values()];
  for (const command of result.commands) command.behavior_peak_demand = commandResourceDemand(graph, command.id);
  const emitted = new Set();
  const emitEvent = (id, time, evidenceIds, checkResult = null) => {
    const definition = nodes[id], key = `${id}@${time}`;
    if (emitted.has(key)) return;
    emitted.add(key);
    const occurrence = { id: `occurrence-${id}-${time}`, definition_id: id, record_kind: 'fixture_event_occurrence', kind: 'event', label: definition.label, at_ns: time, evidence_ids: evidenceIds, check_result_id: checkResult?.id || null, source_id: definition.source_id, actual_event_source: 'authored_fixture' };
    result.events.push(occurrence);
    trace('event_emitted', id, definition.description, time, evidenceIds);
    for (const policy of graph.feedback_policies.filter(policy => policy.event_ids.includes(id))) {
      const response = { policy_id: policy.id, event_id: occurrence.id, agent_id: policy.agent_id, response: policy.response, behavior_ids: [...policy.behavior_ids], decision_ids: [...policy.decision_ids], at_ns: time, scope: 'fixture_only' };
      result.feedback.push(response);
      for (const behaviorId of policy.behavior_ids) {
        const behavior = activeBehaviors.get(behaviorId);
        if (behavior) behavior.status = { pause: 'fixture_paused', abort: 'fixture_aborted', replan: 'fixture_replan_requested' }[policy.response];
      }
      trace('agent_feedback', policy.agent_id, `Declared policy ${policy.id} requests ${policy.response}; no undeclared action is taken.`, time, [occurrence.id]);
    }
  };
  for (const id of scenario.event_ids) if (!nodes[id].trigger) emitEvent(id, startAt, nodes[id].fact_ids);
  for (const [index, step] of timeline.entries()) {
    for (const id of step.updated_fact_ids) {
      const fact = nodes[id];
      if (BigInt(fact.valid_time_ns) <= BigInt(step.at_ns) && BigInt(fact.available_time_ns) <= BigInt(step.at_ns)) {
        result.updated_facts.push({ ...clone(fact), record_kind: 'fixture_state_update', effect_source: 'authored_fixture', at_ns: step.at_ns });
        trace('fixture_state_updated', fact.producer_module_id, 'Apply explicitly authored timed fixture evidence; no physical module was executed.', step.at_ns, [id]);
      }
    }
    for (const checkId of step.check_ids) {
      const check = nodes[checkId], record = evaluateFixtureAdmission(graph, checkId, { fact_ids: scenario.fact_ids, at_ns: step.at_ns, source_id: options.source_id });
      record.phase = index === 0 ? 'start' : 'continue';
      result.check_results.push(record);
      trace('admission_checked', checkId, `Bounded fixture admission: ${record.verdict}. Actual feasibility, permission and suitability remain UNKNOWN.`, step.at_ns, record.evidence.map(evidence => evidence.fact_id));
      for (const behaviorId of check.behavior_ids) {
        const behavior = activeBehaviors.get(behaviorId);
        if (behavior) {
          const latest = new Map(behavior.admission.constraint_results.map(item => [item.constraint_id, item]));
          latest.set(record.constraint_id, { constraint_id: record.constraint_id, verdict: record.verdict, check_result_id: record.id });
          behavior.admission.constraint_results = [...latest.values()];
          behavior.admission.unchecked_constraint_ids = behavior.constraint_ids.filter(id => !latest.has(id));
          const constraintVerdict = [...latest.values()].some(item => item.verdict === 'BLOCK') ? 'BLOCK' : behavior.admission.unchecked_constraint_ids.length || [...latest.values()].some(item => item.verdict === 'UNKNOWN') ? 'UNKNOWN' : 'PERMIT';
          // Re-resolve only at authored targeted checks, never through a hidden per-frame audit.
          behavior.prerequisites = resolveBehaviorPrerequisites(graph, behaviorId, step.at_ns);
          const prerequisiteVerdict = behavior.prerequisites.checks.length ? behavior.prerequisites.verdict : 'NOT_APPLICABLE';
          behavior.admission.constraint_verdict = constraintVerdict;
          behavior.admission.prerequisite_verdict = prerequisiteVerdict;
          behavior.admission.at_ns = step.at_ns;
          behavior.admission.verdict = [constraintVerdict, prerequisiteVerdict].includes('BLOCK') ? 'BLOCK' : constraintVerdict === 'UNKNOWN' || prerequisiteVerdict === 'UNKNOWN' || behavior.condition_result === 'UNKNOWN' ? 'UNKNOWN' : 'PERMIT';
          // A later check cannot silently undo a declared pause/abort/replan response.
          if (!['fixture_paused','fixture_aborted','fixture_replan_requested'].includes(behavior.status)) {
            behavior.status = prerequisiteVerdict === 'BLOCK' ? 'fixture_prerequisite_blocked'
              : constraintVerdict === 'BLOCK' ? 'fixture_blocked'
                : prerequisiteVerdict === 'UNKNOWN' ? 'fixture_prerequisite_unknown'
                  : behavior.condition_result === 'UNKNOWN' ? 'waiting_unknown_condition'
                    : behavior.admission.verdict === 'PERMIT' ? 'fixture_admitted'
                      : record.verdict === 'PERMIT' ? 'fixture_partial_admission' : 'fixture_admission_unknown';
          }
          trace('behavior_admission_combined', behaviorId, `Fixture aggregate ${behavior.admission.verdict}: hard constraints ${constraintVerdict}; lease/receipt prerequisites ${prerequisiteVerdict}. Native permission remains UNKNOWN.`, step.at_ns, [record.id, ...behavior.prerequisites.checks.map(check => check.id)]);
        }
      }
      for (const eventId of step.event_ids) {
        const event = nodes[eventId];
        if (event.trigger?.check_id === checkId && event.trigger.outcome === record.verdict) emitEvent(eventId, step.at_ns, record.evidence.map(evidence => evidence.fact_id), record);
      }
    }
  }
  result.predicate_results = graph.predicates.map(predicate => ({ id: `predicate-result-${predicate.id}-${at}`, predicate_id: predicate.id, record_kind: 'unexecuted_predicate_result', truth: 'UNKNOWN', scope_status: 'UNKNOWN', at_ns: at, evidence: predicate.input_fact_ids.map(id => nodes[id]).map(fact => resolveGraphFact(graph, { entity_id: fact.entity_id, field_id: fact.field_id, fact_ids: scenario.fact_ids, at_ns: at, source_id: options.source_id })).filter(value => value.evidence).map(value => value.evidence), graph_schema_version: graph.schema_version, binding_revision: null, binding_epoch: null, registry_revision: null, history: [], history_status: 'not_supplied', native_engine: 'not_connected' }));
  trace('fixture_finished', scenario.id, 'Binding and bounded-admission fixture completed; no native truth, legal permission, physical effect or remote success is established.', at);
  return result;
}

/** Time-select declared lease/receipt prerequisites. PERMIT is fixture-contract-only. */
export function resolveBehaviorPrerequisites(graph, behaviorId, at_ns) {
  const nodes = indexOf(graph), behavior = nodes[behaviorId], checks = [];
  const result = { behavior_id: behaviorId, at_ns, verdict: 'UNKNOWN', allowed_in_fixture: false, checks, scope: 'fixture_only', actual_assessments: unknown() };
  if (!behavior || !isNs(at_ns)) return { ...result, reason: 'missing_behavior_or_invalid_time' };
  const at = BigInt(at_ns);
  for (const id of behavior.required_allocation_ids || []) {
    const allocation = nodes[id], approval = nodes[allocation?.approval_id];
    let verdict = 'UNKNOWN', reason = 'missing_allocation';
    if (allocation && approval) {
      if (approval.decision === 'rejected' || allocation.status === 'cancelled') { verdict = 'BLOCK'; reason = 'allocation_rejected_or_cancelled'; }
      else if (approval.decision !== 'approved') reason = 'approval_pending';
      else if (!isNs(approval.issued_at_ns) || BigInt(approval.issued_at_ns) > at) reason = 'approval_not_yet_available';
      else if (!isNs(allocation.window?.start_ns) || !isNs(allocation.window?.end_ns)) reason = 'invalid_window';
      else if (at < BigInt(allocation.window.start_ns)) { verdict = 'BLOCK'; reason = 'before_allocated_window'; }
      else if (at >= BigInt(allocation.window.end_ns)) { verdict = 'BLOCK'; reason = 'expired_allocation'; }
      else { verdict = 'PERMIT'; reason = 'declared_fixture_window_available'; }
    }
    checks.push({ kind: 'space_time_allocation', id, verdict, reason, approval_id: allocation?.approval_id || null, window: allocation?.window ? clone(allocation.window) : null });
  }
  for (const id of behavior.required_receipt_ids || []) {
    const receipt = nodes[id];
    let verdict = 'UNKNOWN', reason = 'missing_receipt';
    if (receipt) {
      if (receipt.status === 'rejected') { verdict = 'BLOCK'; reason = 'receipt_rejected'; }
      else if (receipt.status !== 'acknowledged') reason = 'receipt_unconfirmed';
      else if (!isNs(receipt.received_at_ns) || BigInt(receipt.received_at_ns) > at) reason = 'receipt_not_yet_available';
      else { verdict = 'PERMIT'; reason = 'authored_fixture_receipt_available'; }
    }
    checks.push({ kind: 'delivery_receipt', receipt_kind: receipt?.receipt_kind || null, id, verdict, reason });
  }
  result.verdict = checks.some(check => check.verdict === 'BLOCK') ? 'BLOCK' : !checks.length || checks.some(check => check.verdict === 'UNKNOWN') ? 'UNKNOWN' : 'PERMIT';
  result.allowed_in_fixture = checks.length > 0 && result.verdict === 'PERMIT';
  result.reason = checks.length ? 'declared_prerequisites_only_not_actual_permission' : 'not_applicable';
  return result;
}
