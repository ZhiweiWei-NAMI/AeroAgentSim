import type { ChainInstance, EntityKey, FeedCommit, Instant, PredicateTruth } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { entityId } from '../viewport/bindings';
import { displayTime } from '../pages/display-time';
import { entityLabel, readableLabel, stateValue } from '../pages/inspection-format';

/**
 * Cause-to-effect explanations derived only from committed viewer-feed data
 * (chain records, predicate records, facts with their producer, messages and
 * receipts). Nothing here reads engine internals or invents values.
 */
type Fact = FeedCommit['facts'][number];
type Message = FeedCommit['messages'][number];
const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): string | undefined => typeof value === 'string' ? value : undefined;

/** A pointer to something that happened. Other pages build these and call `openCauseTrace`. */
export type CauseRef =
  | { kind: 'event'; cut?: number; atNs?: string; text?: string; entities?: string[] }
  | { kind: 'step'; instanceId: string; revision: number | string }
  | { kind: 'predicate'; contextId: string; cut: number }
  | { kind: 'change'; entity: EntityKey; fieldId: string; cut: number }
  | { kind: 'message'; messageId: string; cut: number }
  | { kind: 'cut'; cut: number; entities?: string[] };

export interface ReadView { entity: EntityKey; entityLabel: string; fieldId: string; fieldLabel: string; value: string; writer: string; writtenAt?: Instant; ref?: CauseRef }
export interface PredicateView {
  predicateId: string; label: string; edge?: string; value: boolean | null; at: Instant; readCut: number;
  expression: string; reads: ReadView[]; ref?: CauseRef; technical: unknown;
}
export interface TriggerView {
  kind: 'predicate' | 'event' | 'timer' | 'command' | 'deadline' | 'start' | 'other';
  summary: string; at?: Instant; source?: string; ref?: CauseRef; predicate?: PredicateView;
}
export interface ActionView { id: string; kind: string; text: string }
export interface StepView {
  instanceId: string; revision: string; chainLabel: string; chainId: string; stepId: string;
  fromState?: string; toState: string; actions: ActionView[]; at: Instant; cut: number; lifecycle: string; technical: unknown;
}
export interface EffectView {
  entity: EntityKey; entityLabel: string; fieldId: string; fieldLabel: string;
  before?: string; after: string; hadBefore: boolean; writer: string; declaredWriter?: string; writerOk: boolean;
  at: Instant; cut: number; unit?: string;
}
export interface FollowUpView { label: string; detail: string; at?: Instant; ref?: CauseRef; kind: 'event' | 'command' | 'step' | 'predicate' | 'none' }
export interface TraceModel {
  focus: 'step' | 'predicate' | 'change' | 'message' | 'moment';
  heading: string; subtitle?: string; at: Instant; cut: number;
  trigger?: TriggerView; predicate?: PredicateView; step?: StepView;
  sender?: { text: string; ref?: CauseRef };
  effects: EffectView[]; followUps: FollowUpView[];
  involved: EntityKey[];
  /** Entity ids the originating feed event named, when the trace was opened from one. */
  eventEntities?: string[];
  choices: Array<{ label: string; ref: CauseRef; at: Instant }>;
  notes: string[];
}

interface StepRec { cut: number; commit: FeedCommit; rec: ChainInstance; key: string }
interface EvalRec { cut: number; rec: PredicateTruth }
interface ActionDef { id: string; kind: string; entity?: { $role?: string }; field?: string; schema?: string; capability?: string; payload?: unknown; duration_ns?: number | string; relation?: string; source?: { $role?: string }; target?: { $role?: string } }
interface TransitionDef { id: string; from?: string; to?: string; actions?: ActionDef[] }
interface Index {
  count: number;
  steps: StepRec[]; stepsByCut: Map<number, StepRec[]>; stepByKey: Map<string, StepRec>;
  byEventSchema: Map<string, StepRec[]>; byCommand: Map<string, StepRec[]>;
  messages: Map<string, { cut: number; message: Message }>; receipts: Map<string, { status: string; cut: number }>;
  evals: EvalRec[]; evalsByEntity?: Map<string, EvalRec[]>;
  chains: Map<string, { transitions: TransitionDef[] }>; predicates: Map<string, { expression?: unknown; parameters?: Record<string, unknown> }>;
}

const revision = (value: unknown): string => isObject(value) && typeof value.$integer === 'string' ? value.$integer : String(value);
const stepKey = (instanceId: string, rev: unknown) => `${instanceId}#${revision(rev)}`;
const generation = (value: unknown): number | string => isObject(value) && typeof value.$integer === 'string' ? value.$integer : (value as number | string);
const refKey = (value: unknown): EntityKey | undefined => {
  const ref = isObject(value) && isObject(value.$ref) ? value.$ref : value;
  return isObject(ref) && typeof ref.id === 'string' ? { id: ref.id, generation: generation(ref.generation ?? 0) } : undefined;
};
export const TERMINAL = new Set(['completed', 'failed', 'canceled']);
/** A chain "step" is a recorded transition; revisions that only update command receipts are not steps. */
const isStep = (rec: ChainInstance) => rec.op !== 'close' && (!!rec.transitionId || TERMINAL.has(rec.lifecycle));

const cache = new WeakMap<TemporalFeedStore, Index>();
function build(store: TemporalFeedStore): Index {
  const old = cache.get(store);
  if (old && old.count === store.commits.length) return old;
  const index: Index = {
    count: store.commits.length, steps: [], stepsByCut: new Map(), stepByKey: new Map(), byEventSchema: new Map(), byCommand: new Map(),
    messages: new Map(), receipts: new Map(), evals: [], chains: new Map(), predicates: new Map(),
  };
  const behaviour = store.header.behaviour as { packages?: Array<{ chains?: Record<string, { transitions?: TransitionDef[] }>; predicates?: Record<string, { expression?: unknown; parameters?: Record<string, unknown> }> }> } | undefined;
  for (const pack of behaviour?.packages ?? []) {
    for (const [id, chain] of Object.entries(pack.chains ?? {})) index.chains.set(id, { transitions: chain.transitions ?? [] });
    for (const [id, predicate] of Object.entries(pack.predicates ?? {})) index.predicates.set(id, predicate);
  }
  const push = <K,>(map: Map<K, StepRec[]>, key: K, value: StepRec) => { const rows = map.get(key); if (rows) rows.push(value); else map.set(key, [value]); };
  for (const commit of store.commits) {
    for (const message of commit.messages) index.messages.set(message.id, { cut: commit.commitIndex, message });
    for (const receipt of commit.receipts) index.receipts.set(receipt.commandId, { status: receipt.status, cut: commit.commitIndex });
    for (const rec of commit.chainInstances ?? []) {
      if (!isStep(rec)) continue;
      const step: StepRec = { cut: commit.commitIndex, commit, rec, key: stepKey(rec.instanceId, rec.revision) };
      index.steps.push(step); index.stepByKey.set(step.key, step); push(index.stepsByCut, step.cut, step);
      const trigger = isObject(rec.trigger) ? rec.trigger : {};
      const event = text(trigger.event); if (event) push(index.byEventSchema, event, step);
      const command = text(trigger.command_id); if (command) push(index.byCommand, command, step);
    }
    for (const rec of commit.predicateTruth ?? []) if (rec.op !== 'close') index.evals.push({ cut: commit.commitIndex, rec });
  }
  cache.set(store, index);
  return index;
}

export const idLabel = (store: TemporalFeedStore, id: string) => {
  const entity = store.entities.get(id);
  return entity ? entityLabel(entity) : readableLabel(id.replace(/:\d+$/, ''));
};
const keyLabel = (store: TemporalFeedStore, key: EntityKey) => idLabel(store, entityId(key));
/** Short plain name for a field: last path segment without its unit suffix. */
export function fieldWords(fieldId: string): string {
  const last = fieldId.slice(fieldId.lastIndexOf('.') + 1).replace(/_(?:enu_m|mps2|mps|deg|rad|ns|m|s|j|w)$/, '');
  return last.replace(/_/g, ' ');
}
export function fieldTitle(store: TemporalFeedStore, fieldId: string): string {
  const parts = fieldId.split('.'); const owner = parts.length > 2 ? parts[parts.length - 2] : '';
  void store; return owner ? `${readableLabel(owner)} ${fieldWords(fieldId)}` : fieldWords(fieldId);
}
const isInstant = (value: unknown): value is { ns: unknown; microstep: unknown } => isObject(value) && 'ns' in value && 'microstep' in value && Object.keys(value).length === 2;
export function showValue(store: TemporalFeedStore, value: unknown, max = 60): string {
  const raw = isObject(value) && typeof value.$number === 'string' ? Number(value.$number) : value;
  if (isInstant(raw)) { const ns = isObject(raw.ns) ? raw.ns.$integer : raw.ns; return `at ${displayTime(String(ns), store.header.start.ns)}`; }
  const plain = stateValue(raw);
  const result = plain ?? exactValue(value);
  return result.length > max ? `${result.slice(0, max - 1)}…` : result;
}
const at = (store: TemporalFeedStore, instant: Instant) => displayTime(instant.ns, store.header.start.ns);

/** Latest value of a field at or before a journal cut. */
export function factAtCut(store: TemporalFeedStore, key: EntityKey, fieldId: string, cut: number): Fact | undefined {
  const rows = store.fieldVersionsOf(entityId(key), fieldId);
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i].commit.commitIndex <= cut) return rows[i].retract ? undefined : rows[i].value as Fact;
  return undefined;
}
function factBefore(store: TemporalFeedStore, key: EntityKey, fieldId: string, cut: number): Fact | undefined {
  return factAtCut(store, key, fieldId, cut - 1);
}
const fieldInfo = (store: TemporalFeedStore, fieldId: string) => store.header.fields.find(row => row.fieldId === fieldId);
const declaredWriter = (store: TemporalFeedStore, fieldId: string) => text((fieldInfo(store, fieldId)?.metadata as { writer_contract?: unknown } | undefined)?.writer_contract);
export function writerName(producer: string): string {
  if (producer === 'behaviour') return 'event chains';
  return `${readableLabel(producer.replace(/^sampled:.*/, 'sampled input'))} engine`;
}

/** Plain-words rendering of a recorded predicate expression. */
export function describeExpression(node: unknown, roles: (role: string) => string, parameters: Record<string, unknown> = {}, nested = false): string {
  if (!isObject(node)) return String(node);
  if ('field' in node) {
    const owner = typeof node.role === 'string' ? `${roles(node.role)}'s ` : '';
    const path = Array.isArray(node.path) && node.path.length ? ` [${node.path.join(',')}]` : '';
    return `${owner}${fieldWords(String(node.field))}${path}`;
  }
  if ('literal' in node) return exactValue(node.literal);
  if ('parameter' in node) { const name = String(node.parameter); return name in parameters ? `${name.replace(/_/g, ' ')} (${exactValue(parameters[name])})` : name; }
  if ('var' in node) return String(node.var);
  const args = Array.isArray(node.args) ? node.args : [];
  const part = (child: unknown) => describeExpression(child, roles, parameters, true);
  const infix: Record<string, string> = { eq: '=', ne: '≠', lt: '<', lte: '≤', gt: '>', gte: '≥', add: '+', sub: '−', mul: '×', div: '÷' };
  const op = String(node.op);
  if (op === 'and' || op === 'or') { const body = args.map(part).join(op === 'and' ? ' AND ' : ' OR '); return nested ? `(${body})` : body; }
  if (op === 'not') return `NOT ${part(args[0])}`;
  if (op in infix && args.length === 2) { const body = `${part(args[0])} ${infix[op]} ${part(args[1])}`; return nested && !['eq', 'ne', 'lt', 'lte', 'gt', 'gte'].includes(op) ? `(${body})` : body; }
  if (op === 'distance') return `distance between ${part(args[0])} and ${part(args[1])}`;
  if (op === 'len') return `number of ${part(args[0])}`;
  return `${op.replace(/_/g, ' ')}(${args.map(part).join(', ')})`;
}

function chainDef(index: Index, templateId: string, transitionId: string | null | undefined): TransitionDef | undefined {
  return transitionId ? index.chains.get(templateId)?.transitions.find(row => row.id === transitionId) : undefined;
}
function rolesOf(rec: ChainInstance): Record<string, EntityKey> {
  const result: Record<string, EntityKey> = {};
  for (const [role, key] of Object.entries(rec.roles)) { const parsed = refKey(key); if (parsed) result[role] = parsed; }
  return result;
}
function payloadRoleIds(payload: unknown, roles: Record<string, EntityKey>): string[] {
  if (Array.isArray(payload)) return payload.flatMap(item => payloadRoleIds(item, roles));
  if (!isObject(payload)) return [];
  if (typeof payload.$role === 'string') { const key = roles[payload.$role]; return key ? [entityId(key)] : []; }
  return Object.values(payload).flatMap(item => payloadRoleIds(item, roles));
}
const subjectIds = (message: Message) => new Set((message.subjects ?? []).map(entityId));
/** Messages of one kind in a step's commit that this transition's declared action produced. */
function producedMessages(step: StepRec, definition: TransitionDef | undefined, kind: 'event' | 'command'): Array<{ action: ActionDef; message: Message }> {
  const roles = rolesOf(step.rec), result: Array<{ action: ActionDef; message: Message }> = [];
  for (const action of definition?.actions ?? []) {
    if (action.kind !== (kind === 'event' ? 'emit' : 'command')) continue;
    const name = kind === 'event' ? action.schema : (action.capability ?? action.schema);
    const wanted = payloadRoleIds(action.payload, roles);
    const message = step.commit.messages.find(row => row.kind === kind && row.source === 'behaviour' && row.schemaId === name && (() => { const have = subjectIds(row); return wanted.every(id => have.has(id)); })());
    if (message) result.push({ action, message });
  }
  return result;
}

function describeAction(store: TemporalFeedStore, step: StepRec, action: ActionDef): string {
  const roles = rolesOf(step.rec);
  const who = (ref?: { $role?: string }) => ref?.$role && roles[ref.$role] ? keyLabel(store, roles[ref.$role]) : 'an entity';
  switch (action.kind) {
    case 'set': return `Update state: ${who(action.entity)} · ${fieldWords(action.field ?? '')}`;
    case 'emit': return `Announce "${readableLabel(action.schema ?? action.id)}"`;
    case 'command': {
      const payload = isObject(action.payload) ? action.payload : {};
      const actor = (isObject(payload.actor) ? payload.actor : isObject(payload.entity) ? payload.entity : undefined) as { $role?: string } | undefined;
      return `Ask ${who(actor)} to "${readableLabel(action.capability ?? action.schema ?? action.id)}"`;
    }
    case 'delay': return `Start a timer "${action.id}" (${Number(action.duration_ns ?? 0) / 1e9} s)`;
    case 'assert_relation': return `Link ${who(action.source)} → ${who(action.target)} (${readableLabel(action.relation ?? 'relation')})`;
    case 'close_relation': return 'Remove a link between entities';
    case 'complete': return 'Finish this chain';
    default: return readableLabel(action.kind);
  }
}

function stepView(store: TemporalFeedStore, index: Index, step: StepRec): StepView {
  const definition = chainDef(index, step.rec.templateId, step.rec.transitionId);
  let fromState = definition?.from;
  if (!fromState) {
    const rows = store.chainRecords(step.rec.instanceId); const here = rows.findIndex(row => stepKey(row.value.instanceId, row.value.revision) === step.key && row.value.op !== 'close');
    for (let i = here - 1; i >= 0; i--) if (rows[i].value.op !== 'close') { fromState = rows[i].value.state; break; }
  }
  return {
    instanceId: step.rec.instanceId, revision: revision(step.rec.revision), chainLabel: readableLabel(step.rec.templateId), chainId: step.rec.templateId,
    stepId: step.rec.transitionId ?? step.rec.lifecycle, fromState, toState: step.rec.state, lifecycle: step.rec.lifecycle,
    actions: (definition?.actions ?? []).map(action => ({ id: action.id, kind: action.kind, text: describeAction(store, step, action) })),
    at: step.commit.at, cut: step.cut, technical: { instanceId: step.rec.instanceId, revision: step.rec.revision, transition: step.rec.transitionId, trigger: step.rec.trigger, causes: step.rec.causes },
  };
}

function effectsOf(store: TemporalFeedStore, index: Index, step: StepRec): EffectView[] {
  const definition = chainDef(index, step.rec.templateId, step.rec.transitionId), roles = rolesOf(step.rec);
  const wanted: Array<{ id: string; field: string }> = [];
  if (definition) for (const action of definition.actions ?? []) if (action.kind === 'set' && action.entity?.$role && roles[action.entity.$role] && action.field) wanted.push({ id: entityId(roles[action.entity.$role]), field: action.field });
  const own = new Set(Object.values(roles).map(entityId));
  const facts = step.commit.facts.filter(fact => fact.producer === 'behaviour' && (definition ? wanted.some(row => row.id === entityId(fact.entity) && row.field === fact.fieldId) : own.has(entityId(fact.entity)) && !fact.fieldId.startsWith('aas.behaviour.')));
  const latest = new Map<string, Fact>(); for (const fact of facts) latest.set(`${entityId(fact.entity)}|${fact.fieldId}`, fact);
  return [...latest.values()].map(fact => effectView(store, fact, step.cut));
}
function effectView(store: TemporalFeedStore, fact: Fact, cut: number, at?: Instant): EffectView {
  const previous = factBefore(store, fact.entity, fact.fieldId, cut), declared = declaredWriter(store, fact.fieldId);
  const field = fieldInfo(store, fact.fieldId);
  const unit = field?.unit && field.unit !== '1' ? field.unit : undefined;
  return {
    entity: fact.entity, entityLabel: keyLabel(store, fact.entity), fieldId: fact.fieldId, fieldLabel: fieldWords(fact.fieldId),
    before: previous ? showValue(store, previous.value) : undefined, hadBefore: !!previous, after: showValue(store, fact.value),
    writer: fact.producer, declaredWriter: declared, writerOk: !declared || declared === fact.producer, at: at ?? fact.validFrom ?? store.commits.find(row => row.commitIndex === cut)!.at, cut, unit,
  };
}

function predicateReads(store: TemporalFeedStore, index: Index, evaluation: { acquired?: unknown; roles?: unknown; readCut?: { index: number } }): ReadView[] {
  const acquired = isObject(evaluation.acquired) ? evaluation.acquired : {}, roles = isObject(evaluation.roles) ? evaluation.roles : {};
  const readCut = evaluation.readCut?.index ?? Number.MAX_SAFE_INTEGER, rows: ReadView[] = [];
  for (const [slot, stamp] of Object.entries(acquired)) {
    const dot = slot.indexOf('.'); if (dot < 0) continue;
    const role = slot.slice(0, dot), fieldId = slot.slice(dot + 1), key = refKey(roles[role]);
    if (!key) continue;
    const fact = factAtCut(store, key, fieldId, readCut), producer = fact?.producer ?? text(isObject(stamp) ? stamp.producer : undefined) ?? 'unknown';
    let ref: CauseRef | undefined;
    if (fact) {
      const cut = lastWriteCut(store, key, fieldId, readCut);
      const writing = cut === undefined ? undefined : (index.stepsByCut.get(cut) ?? []).find(step => effectsOf(store, index, step).some(effect => effect.fieldId === fieldId && entityId(effect.entity) === entityId(key)));
      ref = writing ? { kind: 'step', instanceId: writing.rec.instanceId, revision: revision(writing.rec.revision) } : cut === undefined ? undefined : { kind: 'change', entity: key, fieldId, cut };
    }
    rows.push({ entity: key, entityLabel: keyLabel(store, key), fieldId, fieldLabel: fieldWords(fieldId), value: fact ? showValue(store, fact.value) : 'not recorded', writer: producer, writtenAt: fact?.validFrom, ref });
  }
  return rows;
}
function lastWriteCut(store: TemporalFeedStore, key: EntityKey, fieldId: string, cut: number): number | undefined {
  const rows = store.fieldVersionsOf(entityId(key), fieldId);
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i].commit.commitIndex <= cut) return rows[i].commit.commitIndex;
  return undefined;
}

function predicateView(store: TemporalFeedStore, index: Index, base: { predicateId: string; edge?: string; value: boolean | null; at: Instant; readCut: number; evaluation: { acquired?: unknown; roles?: unknown; readCut?: { index: number } }; ref?: CauseRef }): PredicateView {
  const definition = index.predicates.get(base.predicateId), roles = isObject(base.evaluation.roles) ? base.evaluation.roles : {};
  const roleName = (role: string) => { const key = refKey(roles[role]); return key ? keyLabel(store, key) : role; };
  return {
    predicateId: base.predicateId, label: readableLabel(base.predicateId), edge: base.edge, value: base.value, at: base.at, readCut: base.readCut,
    expression: definition?.expression ? describeExpression(definition.expression, roleName, definition.parameters ?? {}) : '',
    reads: predicateReads(store, index, base.evaluation), ref: base.ref, technical: base.evaluation,
  };
}
const evalPredicateView = (store: TemporalFeedStore, index: Index, rec: PredicateTruth, cut: number) =>
  predicateView(store, index, { predicateId: rec.predicateId, value: rec.value, at: rec.evaluatedAt, readCut: rec.readCut.index, evaluation: rec, ref: { kind: 'predicate', contextId: rec.contextId, cut } });

function triggerOf(store: TemporalFeedStore, index: Index, step: StepRec): TriggerView {
  const trigger = isObject(step.rec.trigger) ? step.rec.trigger : {};
  if (typeof trigger.predicate === 'string' && isObject(trigger.evaluation)) {
    const evaluation = trigger.evaluation as { evaluatedAt?: Instant; readCut?: { index: number; at: Instant }; roles?: unknown; acquired?: unknown; contextId?: string };
    const when = evaluation.evaluatedAt ?? step.commit.at, edge = text(trigger.edge);
    const view = predicateView(store, index, { predicateId: trigger.predicate, edge, value: true, at: when, readCut: evaluation.readCut?.index ?? step.cut, evaluation, ref: evaluation.contextId ? { kind: 'predicate', contextId: evaluation.contextId, cut: step.cut } : undefined });
    return { kind: 'predicate', summary: `${edge === 'entered' ? 'The condition' : 'The condition'} "${view.label}" became true`, at: when, predicate: view, ref: view.ref };
  }
  if (typeof trigger.event === 'string') {
    const found = findSender(store, index, trigger.event, trigger.payload, step.cut);
    return { kind: 'event', summary: `The event "${readableLabel(trigger.event)}" arrived`, at: found?.message ? found.message.message.at : step.commit.at, source: found?.text, ref: found?.ref };
  }
  if (typeof trigger.timer === 'string') {
    const ref: CauseRef | undefined = typeof trigger.instance === 'string' && trigger.revision !== undefined && index.stepByKey.has(stepKey(trigger.instance, trigger.revision)) ? { kind: 'step', instanceId: trigger.instance, revision: revision(trigger.revision) } : undefined;
    return { kind: 'timer', summary: `The timer "${trigger.timer}" ran out`, at: step.commit.at, source: ref ? 'The timer was started by an earlier step of this chain' : undefined, ref };
  }
  if (typeof trigger.command_id === 'string') {
    const sent = index.messages.get(trigger.command_id), status = text(trigger.status);
    const sender = sent ? (index.stepsByCut.get(sent.cut) ?? []).find(row => row.rec.instanceId === step.rec.instanceId) : undefined;
    return { kind: 'command', summary: `The command "${readableLabel(text(trigger.receipt) ?? 'command')}" ${status ? `finished: ${status}` : 'reported back'}`, at: step.commit.at, source: sent ? `Sent to ${writerName(String(sent.message.target ?? 'an engine'))}` : undefined, ref: sender ? { kind: 'step', instanceId: sender.rec.instanceId, revision: revision(sender.rec.revision) } : undefined };
  }
  if (trigger.deadline !== undefined) return { kind: 'deadline', summary: 'A time limit ran out', at: step.commit.at };
  if (trigger.instance === 'activated') return { kind: 'start', summary: 'The chain was just activated', at: step.commit.at };
  return { kind: 'other', summary: 'Triggered by a recorded condition', at: step.commit.at };
}
function findSender(store: TemporalFeedStore, index: Index, schema: string, payload: unknown, before: number): { text: string; ref?: CauseRef; message?: { cut: number; message: Message } } | undefined {
  const wanted = exactValue(payload);
  let best: { cut: number; message: Message } | undefined;
  for (const [, row] of index.messages) if (row.cut <= before && row.message.kind === 'event' && row.message.schemaId === schema && exactValue(row.message.payload) === wanted && (!best || row.cut > best.cut)) best = row;
  if (!best) return undefined;
  const sources = best.message.source;
  if (sources === 'behaviour') {
    const origin = (index.stepsByCut.get(best.cut) ?? []).find(candidate => producedMessages(candidate, chainDef(index, candidate.rec.templateId, candidate.rec.transitionId), 'event').some(item => item.message.id === best!.message.id));
    if (origin) return { text: `Announced by the chain "${readableLabel(origin.rec.templateId)}"`, ref: { kind: 'step', instanceId: origin.rec.instanceId, revision: revision(origin.rec.revision) }, message: best };
  }
  void store;
  return { text: `Sent by the ${writerName(sources)}`, ref: { kind: 'message', messageId: best.message.id, cut: best.cut }, message: best };
}

function evalsByEntity(index: Index): Map<string, EvalRec[]> {
  if (index.evalsByEntity) return index.evalsByEntity;
  const map = new Map<string, EvalRec[]>();
  for (const row of index.evals) for (const key of Object.values(row.rec.roles)) { const id = entityId(key); const rows = map.get(id); if (rows) rows.push(row); else map.set(id, [row]); }
  index.evalsByEntity = map; return map;
}

function followUpsOfStep(store: TemporalFeedStore, index: Index, step: StepRec, effects: EffectView[]): FollowUpView[] {
  const definition = chainDef(index, step.rec.templateId, step.rec.transitionId), result: FollowUpView[] = [];
  for (const item of producedMessages(step, definition, 'event')) {
    const message = item.message;
    const receivers = (index.byEventSchema.get(message.schemaId) ?? []).filter(row => row.cut >= step.cut && exactValue((row.rec.trigger as { payload?: unknown }).payload) === exactValue(message.payload));
    result.push({ kind: 'event', label: `Announced "${readableLabel(message.schemaId)}"`, detail: receivers.length ? `${receivers.length} chain step${receivers.length > 1 ? 's' : ''} reacted` : 'No event-chain step reacted to it', at: message.at });
    for (const receiver of receivers.slice(0, 4)) result.push(stepFollowUp(store, index, receiver, 'reacted to the announcement'));
  }
  for (const item of producedMessages(step, definition, 'command')) {
    const message = item.message, receipt = index.receipts.get(message.id);
    result.push({ kind: 'command', label: `Asked ${message.target ? writerName(String(message.target)) : 'an engine'} to "${readableLabel(message.schemaId)}"`, detail: receipt ? `Result: ${receipt.status}` : 'No result recorded yet', at: message.at });
    for (const receiver of index.byCommand.get(message.id) ?? []) if (receiver.cut >= step.cut && receiver.key !== step.key) result.push(stepFollowUp(store, index, receiver, 'handled the result'));
  }
  const seen = new Set<string>();
  for (const effect of effects) {
    const stamp = effect.at.ns;
    for (const row of evalsByEntity(index).get(entityId(effect.entity)) ?? []) {
      if (row.cut <= step.cut || seen.has(row.rec.contextId)) continue;
      const matched = Object.entries(row.rec.acquired ?? {}).some(([slot, stampInfo]) => {
        const dot = slot.indexOf('.'); if (dot < 0 || slot.slice(dot + 1) !== effect.fieldId) return false;
        const key = row.rec.roles[slot.slice(0, dot)]; return key && entityId(key) === entityId(effect.entity) && String(stampInfo.numerator) === stamp;
      });
      if (!matched) continue;
      seen.add(row.rec.contextId);
      const others = Object.values(row.rec.roles).filter(key => entityId(key) !== entityId(effect.entity)).slice(0, 2).map(key => keyLabel(store, key)).join(', ');
      result.push({ kind: 'predicate', label: `The condition "${readableLabel(row.rec.predicateId)}"${others ? ` (${others})` : ''} was re-checked using the new ${effect.fieldLabel}`, detail: row.rec.value === null ? 'Result unknown' : `Result: ${row.rec.value ? 'true' : 'false'}`, at: row.rec.evaluatedAt, ref: { kind: 'predicate', contextId: row.rec.contextId, cut: row.cut } });
      if (seen.size >= 4) break;
    }
  }
  return result.slice(0, 16);
}
function stepFollowUp(store: TemporalFeedStore, index: Index, step: StepRec, verb: string): FollowUpView {
  const view = stepView(store, index, step);
  return { kind: 'step', label: `${view.chainLabel}: ${stepSentence(view)}`, detail: `${verb} · ${at(store, step.commit.at)}`, at: step.commit.at, ref: { kind: 'step', instanceId: step.rec.instanceId, revision: revision(step.rec.revision) } };
}
export const stepSentence = (view: Pick<StepView, 'fromState' | 'toState' | 'stepId'>) => view.fromState && view.fromState !== view.toState ? `${readableLabel(view.fromState)} → ${readableLabel(view.toState)}` : `step "${view.stepId}" → ${readableLabel(view.toState)}`;

function involvedOf(store: TemporalFeedStore, model: Pick<TraceModel, 'step' | 'effects' | 'trigger' | 'predicate'>, step?: StepRec, extra: EntityKey[] = []): EntityKey[] {
  const map = new Map<string, EntityKey>();
  const add = (key: EntityKey) => { const type = store.entities.get(entityId(key))?.typeId; if (type !== 'aas:BehaviourInstance' && !key.id.startsWith('behaviour:')) map.set(entityId(key), key); };
  if (step) Object.values(rolesOf(step.rec)).forEach(add);
  model.effects.forEach(effect => add(effect.entity));
  model.trigger?.predicate?.reads.forEach(read => add(read.entity));
  model.predicate?.reads.forEach(read => add(read.entity));
  extra.forEach(add);
  return [...map.values()];
}

const LEAN_NOTE = 'This run keeps what ran, what it changed and what it set off. A run recorded with full provenance would also list every individual input record that each written value was computed from.';

function explainStep(store: TemporalFeedStore, index: Index, step: StepRec): TraceModel {
  const view = stepView(store, index, step), trigger = triggerOf(store, index, step), effects = effectsOf(store, index, step);
  const model: TraceModel = {
    focus: 'step', heading: `${view.chainLabel}: ${stepSentence(view)}`, subtitle: `at ${at(store, step.commit.at)}`, at: step.commit.at, cut: step.cut,
    trigger, step: view, effects, followUps: followUpsOfStep(store, index, step, effects), involved: [], choices: [], notes: [LEAN_NOTE],
  };
  if (!view.actions.length) model.notes.unshift('This run did not include the chain definition, so individual actions are not listed.');
  model.involved = involvedOf(store, model, step);
  return model;
}

function explainPredicate(store: TemporalFeedStore, index: Index, contextId: string, cut: number): TraceModel | undefined {
  const rows = store.predicateRecords(contextId).filter(row => row.value.op !== 'close' && row.commit.commitIndex <= cut);
  const row = rows[rows.length - 1]; if (!row) return undefined;
  const view = evalPredicateView(store, index, row.value, row.commit.commitIndex);
  const followUps: FollowUpView[] = [];
  for (const step of index.steps) {
    if (step.cut < row.commit.commitIndex) continue;
    const trigger = isObject(step.rec.trigger) ? step.rec.trigger : {};
    if (isObject(trigger.evaluation) && trigger.evaluation.contextId === contextId) followUps.push(stepFollowUp(store, index, step, 'ran because of it'));
    if (followUps.length >= 6) break;
  }
  if (!followUps.length) followUps.push({ kind: 'none', label: 'No chain step is waiting on this condition', detail: 'It was recorded but nothing reacted to it.' });
  const model: TraceModel = {
    focus: 'predicate', heading: `"${view.label}" is ${view.value === null ? 'unknown' : view.value ? 'true' : 'false'}`, subtitle: `checked at ${at(store, view.at)}`, at: view.at, cut: row.commit.commitIndex,
    predicate: view, effects: [], followUps, involved: [], choices: [], notes: [LEAN_NOTE],
  };
  model.involved = involvedOf(store, model);
  return model;
}

function explainMessage(store: TemporalFeedStore, index: Index, messageId: string): TraceModel | undefined {
  const found = index.messages.get(messageId); if (!found) return undefined;
  const { message, cut } = found, followUps: FollowUpView[] = [];
  const origin = message.source === 'behaviour' ? (index.stepsByCut.get(cut) ?? []).find(step => producedMessages(step, chainDef(index, step.rec.templateId, step.rec.transitionId), message.kind).some(item => item.message.id === message.id)) : undefined;
  const receivers = message.kind === 'event' ? (index.byEventSchema.get(message.schemaId) ?? []).filter(row => row.cut >= cut && exactValue((row.rec.trigger as { payload?: unknown }).payload) === exactValue(message.payload)) : index.byCommand.get(message.id) ?? [];
  for (const receiver of receivers.slice(0, 6)) followUps.push(stepFollowUp(store, index, receiver, 'reacted'));
  if (!followUps.length) followUps.push({ kind: 'none', label: 'Nothing reacted to this', detail: 'No chain step was waiting for it.' });
  const model: TraceModel = {
    focus: 'message', heading: `${readableLabel(message.schemaId)}`, subtitle: `${message.kind === 'event' ? 'announced' : 'requested'} at ${at(store, message.at)}`, at: message.at, cut,
    sender: origin ? { text: `Sent by the chain "${readableLabel(origin.rec.templateId)}"`, ref: { kind: 'step', instanceId: origin.rec.instanceId, revision: revision(origin.rec.revision) } } : { text: `Sent by the ${writerName(message.source)}` },
    effects: [], followUps, involved: [], choices: [], notes: [LEAN_NOTE],
  };
  model.involved = involvedOf(store, model, undefined, message.subjects ?? []);
  return model;
}

function explainChange(store: TemporalFeedStore, index: Index, entity: EntityKey, fieldId: string, cut: number): TraceModel | undefined {
  const commit = store.commits.find(row => row.commitIndex === cut); if (!commit) return undefined;
  const writing = (index.stepsByCut.get(cut) ?? []).find(step => effectsOf(store, index, step).some(effect => effect.fieldId === fieldId && entityId(effect.entity) === entityId(entity)));
  if (writing) return explainStep(store, index, writing);
  const fact = [...commit.facts].reverse().find(row => row.fieldId === fieldId && entityId(row.entity) === entityId(entity));
  if (!fact) return undefined;
  const effect = effectView(store, fact, cut, commit.at), followUps: FollowUpView[] = [];
  for (const row of evalsByEntity(index).get(entityId(entity)) ?? []) {
    if (row.cut <= cut) continue;
    const used = Object.entries(row.rec.acquired ?? {}).some(([slot, stamp]) => slot.slice(slot.indexOf('.') + 1) === fieldId && String(stamp.numerator) === effect.at.ns);
    if (used) followUps.push({ kind: 'predicate', label: `The condition "${readableLabel(row.rec.predicateId)}" was checked using it`, detail: `Result: ${row.rec.value ? 'true' : 'false'}`, at: row.rec.evaluatedAt, ref: { kind: 'predicate', contextId: row.rec.contextId, cut: row.cut } });
    if (followUps.length >= 4) break;
  }
  const model: TraceModel = {
    focus: 'change', heading: `${keyLabel(store, entity)}: ${effect.fieldLabel} changed`, subtitle: `at ${at(store, commit.at)}`, at: commit.at, cut,
    sender: { text: `Written by the ${writerName(fact.producer)}, not by an event chain` }, effects: [effect], followUps, involved: [], choices: [], notes: [LEAN_NOTE],
  };
  model.involved = involvedOf(store, model, undefined, [entity]);
  return model;
}

/** Steps worth showing for a moment: those involving the named entities, emitters first. */
function momentSteps(store: TemporalFeedStore, index: Index, cut: number, entities?: string[]): StepRec[] {
  const steps = index.stepsByCut.get(cut) ?? [];
  const wanted = entities?.length ? new Set(entities) : undefined;
  const scoped = wanted ? steps.filter(step => Object.values(rolesOf(step.rec)).some(key => wanted.has(key.id))) : steps;
  const weight = (step: StepRec) => {
    const definition = chainDef(index, step.rec.templateId, step.rec.transitionId);
    return (isObject(step.rec.trigger) && typeof step.rec.trigger.predicate === 'string' ? 2 : 0) + (definition?.actions?.some(row => row.kind === 'emit' || row.kind === 'set') ? 1 : 0);
  };
  void store;
  return [...scoped].sort((a, b) => weight(b) - weight(a));
}

/** Build the cause-to-effect story for a reference; undefined if the referenced record is not loaded. */
export function explain(store: TemporalFeedStore, ref: CauseRef): TraceModel | undefined {
  const index = build(store);
  switch (ref.kind) {
    case 'step': { const step = index.stepByKey.get(stepKey(ref.instanceId, ref.revision)); return step ? explainStep(store, index, step) : undefined; }
    case 'predicate': return explainPredicate(store, index, ref.contextId, ref.cut);
    case 'message': return explainMessage(store, index, ref.messageId);
    case 'change': return explainChange(store, index, ref.entity, ref.fieldId, ref.cut);
    case 'event':
    case 'cut': {
      let cut = ref.cut, entities = ref.entities;
      if (cut === undefined && ref.kind === 'event') {
        const match = store.commits.find(commit => (commit.events ?? []).some(event => (!ref.atNs || event.at_ns === ref.atNs) && (!ref.text || event.text === ref.text)));
        cut = match?.commitIndex;
        if (!entities && match) entities = (match.events ?? []).find(event => !ref.text || event.text === ref.text)?.entities;
      }
      if (cut === undefined) return undefined;
      const commit = store.commits.find(row => row.commitIndex === cut); if (!commit) return undefined;
      const steps = momentSteps(store, index, cut, entities);
      const named = (model: TraceModel) => { if (entities?.length) model.eventEntities = entities; return model; };
      if (steps.length) {
        const model = named(explainStep(store, index, steps[0]));
        model.choices = steps.slice(1, 8).map(step => { const view = stepView(store, index, step); return { label: `${view.chainLabel}: ${stepSentence(view)}`, ref: { kind: 'step', instanceId: step.rec.instanceId, revision: revision(step.rec.revision) } as CauseRef, at: step.commit.at }; });
        return model;
      }
      const ids = entities?.length ? new Set(entities) : undefined;
      const messages = commit.messages.filter(message => message.kind === 'event' && !message.schemaId.startsWith('aas.behaviour.') && (!ids || (message.subjects ?? []).some(key => ids.has(key.id))));
      if (messages.length) {
        const model = named(explainMessage(store, index, messages[0].id)!);
        model.choices = messages.slice(1, 8).map(message => ({ label: readableLabel(message.schemaId), ref: { kind: 'message', messageId: message.id, cut } as CauseRef, at: message.at }));
        return model;
      }
      return {
        focus: 'moment', heading: 'Nothing recorded as a cause here', subtitle: `at ${at(store, commit.at)}`, at: commit.at, cut, effects: [], followUps: [], involved: [], choices: [],
        notes: ['No event-chain step or announcement was recorded at this moment. Only engines wrote state here; select a field in "What changed" to see its writer.'],
      };
    }
  }
}

export interface Moment { key: string; label: string; detail: string; at: Instant; cut: number; ref: CauseRef; kind: 'event' | 'step' | 'condition' }
/** Notable moments involving one entity: business events, chain steps and conditions that became true. */
export function momentsFor(store: TemporalFeedStore, key: EntityKey): Moment[] {
  const index = build(store), id = key.id, result: Moment[] = [];
  for (const commit of store.commits) {
    for (const [ordinal, event] of (commit.events ?? []).entries()) if (event.entities.includes(id)) result.push({ key: `e${commit.commitIndex}/${ordinal}`, label: event.text.length > 70 ? `${event.text.slice(0, 69)}…` : event.text, detail: 'Event', at: commit.at, cut: commit.commitIndex, kind: 'event', ref: { kind: 'event', cut: commit.commitIndex, text: event.text, atNs: event.at_ns, entities: event.entities } });
  }
  for (const step of index.steps) {
    if (!Object.values(rolesOf(step.rec)).some(role => role.id === id)) continue;
    const view = stepView(store, index, step);
    if (!view.actions.length && step.rec.templateId === 'traffic.road_follow') continue;
    result.push({ key: `s${step.key}`, label: `${view.chainLabel}: ${stepSentence(view)}`, detail: 'Chain step', at: step.commit.at, cut: step.cut, kind: 'step', ref: { kind: 'step', instanceId: step.rec.instanceId, revision: revision(step.rec.revision) } });
  }
  const previous = new Map<string, boolean | null>();
  for (const row of index.evals) {
    const before = previous.get(row.rec.contextId); previous.set(row.rec.contextId, row.rec.value);
    if (row.rec.value !== true || before === true || !Object.values(row.rec.roles).some(role => role.id === id)) continue;
    result.push({ key: `p${row.rec.contextId}/${row.cut}`, label: `"${readableLabel(row.rec.predicateId)}" became true`, detail: 'Condition', at: row.rec.evaluatedAt, cut: row.cut, kind: 'condition', ref: { kind: 'predicate', contextId: row.rec.contextId, cut: row.cut } });
  }
  return result.sort((a, b) => a.cut - b.cut || a.key.localeCompare(b.key));
}

export interface ChangeRow { key: string; fieldId: string; fieldLabel: string; before?: string; after: string; retracted: boolean; at: Instant; cut: number; writer: string; cause: string; ref: CauseRef }
export interface ChangeSummary { fieldId: string; fieldLabel: string; updates: number; writer: string }
/** Field changes of one entity over time; fields that update continuously are summarised instead of listed. */
export function changesFor(store: TemporalFeedStore, key: EntityKey, limitPerField = 24): { rows: ChangeRow[]; continuous: ChangeSummary[] } {
  const index = build(store), id = entityId(key), rows: ChangeRow[] = [], continuous: ChangeSummary[] = [];
  for (const fieldId of store.fieldIdsOf(id)) {
    if (fieldId.startsWith('aas.behaviour.') || /(?:digest|hash)/i.test(fieldId)) continue;
    const versions = store.fieldVersionsOf(id, fieldId);
    if (versions.length > limitPerField) { continuous.push({ fieldId, fieldLabel: fieldWords(fieldId), updates: versions.length, writer: (versions[versions.length - 1].value as Fact).producer ?? 'engine' }); continue; }
    let previous: Fact | undefined;
    for (const version of versions) {
      if (version.retract) { rows.push({ key: `${fieldId}/${version.commit.commitIndex}/r`, fieldId, fieldLabel: fieldWords(fieldId), before: previous ? showValue(store, previous.value) : undefined, after: 'removed', retracted: true, at: version.commit.at, cut: version.commit.commitIndex, writer: 'engine', cause: 'Value withdrawn', ref: { kind: 'change', entity: key, fieldId, cut: version.commit.commitIndex } }); previous = undefined; continue; }
      const fact = version.value as Fact, cut = version.commit.commitIndex;
      const writing = fact.producer === 'behaviour' ? (index.stepsByCut.get(cut) ?? []).find(step => effectsOf(store, index, step).some(effect => effect.fieldId === fieldId && entityId(effect.entity) === id)) : undefined;
      const cause = writing ? `${readableLabel(writing.rec.templateId)} (step "${writing.rec.transitionId ?? writing.rec.lifecycle}")` : `${writerName(fact.producer)}`;
      rows.push({ key: `${fieldId}/${cut}/${rows.length}`, fieldId, fieldLabel: fieldWords(fieldId), before: previous ? showValue(store, previous.value) : undefined, after: showValue(store, fact.value), retracted: false, at: version.commit.at, cut, writer: fact.producer, cause, ref: { kind: 'change', entity: key, fieldId, cut } });
      previous = fact;
    }
  }
  return { rows: rows.sort((a, b) => a.cut - b.cut || a.fieldId.localeCompare(b.fieldId)), continuous };
}

/** Test hook: forget cached indexes (live runs rebuild automatically when commits arrive). */
export function resetTraceIndex(store: TemporalFeedStore) { cache.delete(store); }
