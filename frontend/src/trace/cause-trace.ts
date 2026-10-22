import type { ChainInstance, EntityKey, FeedCommit, Instant, PredicateTruth } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { entityId } from '../viewport/bindings';
import { displayTime } from '../pages/display-time';
import { readableLabel, stateValue } from '../pages/inspection-format';
import { displayName } from '../viewport/semantic';
import { eventNarrative } from '../newcomers/presentation';
import { translate } from '../i18n/translate';

const translator = (locale: string) => (key: string, vars: Record<string, unknown> = {}) => translate(locale, key, vars);

/**
 * Cause-to-effect explanations derived only from committed viewer-feed data
 * (chain records, predicate records, facts with their producer, messages and
 * receipts). Nothing here reads engine internals or invents values.
 */
type Fact = FeedCommit['facts'][number];
type Message = FeedCommit['messages'][number];
const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): string | undefined => typeof value === 'string' ? value : undefined;
const storyLabels: Record<string, string> = {
  'lowalt.operator.restaurant_delay': '操作员出餐延迟', 'lowalt.food.preparation_delayed': '备餐延迟',
  'lowalt.food.ready': '出餐就绪', 'lowalt.food.delivered': '餐食送达',
  'lowalt.task.created': '任务释放', 'lowalt.task.assigned': '任务派发',
  'lowalt.task.started': '任务启动', 'lowalt.task.picked_up': '完成取件',
  'lowalt.task.completed': '任务完成', 'lowalt.task.replanned': '任务改航',
  'lowalt.operator.investigate': '操作员请求调查', 'lowalt.operator.close_vertiport': '操作员关闭起降场',
  'lowalt.operator.close_air_taxi_port': '操作员关闭载客起降场', 'lowalt.site.closed': '起降场关闭',
  'lowalt.operator.change_dropoff': '操作员变更收货点', 'lowalt.parcel.dropoff_changed': '收货点已变更',
  'lowalt.operator.prioritize_medical': '操作员提升医疗优先级', 'lowalt.medical.prioritized': '医疗优先级已提升',
  'lowalt.operator.report_defect': '操作员报告线路缺陷', 'lowalt.inspection.defect_reported': '线路缺陷复查已释放',
  'lowalt.inspection.observed': '线路实际观测', 'lowalt.operator.pause_spraying': '操作员请求停喷',
  'lowalt.spraying.paused': '喷洒已暂停', 'lowalt.spraying.resumed': '喷洒已恢复',
  'lowalt.spraying.applied': '实际喷洒观测', 'lowalt.operator.rejected': '操作请求被拒绝',
};
const businessFields: Record<string, { en: string; zh: string; clock?: true }> = {
  'lowalt.task_destination': { en: 'task destination', zh: '任务目的地' },
  'lowalt.task_priority': { en: 'task priority', zh: '任务优先级' },
  'lowalt.task_state': { en: 'task state', zh: '任务状态' },
  'lowalt.site_open': { en: 'site open', zh: '起降场开放状态' },
  'lowalt.task_created_ns': { en: 'task release time', zh: '任务释放时间', clock: true },
  'lowalt.task_started_ns': { en: 'task start time', zh: '任务启动时间', clock: true },
  'lowalt.task_completed_ns': { en: 'task completion time', zh: '任务完成时间', clock: true },
  'lowalt.food_expected_ready_ns': { en: 'expected readiness', zh: '预计出餐时间', clock: true },
  'lowalt.food_preparation_started_ns': { en: 'preparation start time', zh: '开始备餐时间', clock: true },
  'lowalt.food_ready_ns': { en: 'ready time', zh: '实际出餐时间', clock: true },
  'lowalt.food_delivered_ns': { en: 'delivery time', zh: '餐食送达时间', clock: true },
  'lowalt.food_deadline_ns': { en: 'delivery deadline', zh: '餐食送达截止时间', clock: true },
};
const messageLabel = (schema: string, locale: string) => schema === 'aas.runtime.inject_event'
  ? locale === 'zh-CN' ? '操作员请求' : 'Operator request'
  : locale === 'zh-CN' && storyLabels[schema] ? storyLabels[schema] : readableLabel(schema, locale);

/** A pointer to something that happened. Other pages build these and call `openCauseTrace`. */
export type CauseRef =
  | { kind: 'event'; cut?: number; atNs?: string; text?: string; entities?: string[] }
  | { kind: 'step'; instanceId: string; revision: number | string }
  | { kind: 'predicate'; contextId: string; cut: number }
  | { kind: 'change'; entity: EntityKey; fieldId: string; cut: number }
  | { kind: 'message'; messageId: string; cut: number }
  | { kind: 'cut'; cut: number; entities?: string[] };

export interface ReadView { entity: EntityKey; entityLabel: string; fieldId: string; fieldLabel: string; value: string; writer: string; writtenAt?: Instant; ref?: CauseRef }
/**
 * How strongly two records are tied. `recorded` is a recorded handle: a
 * schema/payload or provenance match, an acquired input stamp, or a chain
 * instance. `same-cut` only means both appear in the same committed cut with
 * the same source/subject. Neither is a causal attribution.
 */
export type LinkBasis = 'recorded' | 'same-cut';
export interface PredicateView {
  predicateId: string; label: string; edge?: string; value: boolean | null; at: Instant; readCut: number;
  expression: string; reads: ReadView[]; ref?: CauseRef; technical: unknown;
}
export interface TriggerView {
  kind: 'predicate' | 'event' | 'timer' | 'command' | 'deadline' | 'start' | 'other';
  summary: string; at?: Instant; source?: string; ref?: CauseRef; predicate?: PredicateView;
  /** Present when the trigger is a recorded/correlated link rather than a direct predicate or timer declaration. */
  basis?: LinkBasis;
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
  /** `same-cut` when this fact merely shares source/subject with the focus record. */
  basis?: LinkBasis;
}
export interface FollowUpView { label: string; detail: string; at?: Instant; ref?: CauseRef; kind: 'event' | 'command' | 'step' | 'predicate' | 'none'; basis?: LinkBasis }
export interface TraceModel {
  focus: 'step' | 'predicate' | 'change' | 'message' | 'moment';
  heading: string; subtitle?: string; at: Instant; cut: number;
  trigger?: TriggerView; predicate?: PredicateView; step?: StepView;
  sender?: { text: string; ref?: CauseRef; basis?: LinkBasis };
  effects: EffectView[]; followUps: FollowUpView[];
  involved: EntityKey[];
  /** Entity ids the originating feed event named, when the trace was opened from one. */
  eventEntities?: string[];
  choices: Array<{ label: string; ref: CauseRef; at: Instant; basis?: LinkBasis }>;
  notes: string[];
}

interface StepRec { cut: number; commit: FeedCommit; rec: ChainInstance; key: string }
interface EvalRec { cut: number; rec: PredicateTruth }
interface ActionDef { id: string; kind: string; entity?: { $role?: string }; field?: string; schema?: string; capability?: string; payload?: unknown; duration_ns?: number | string; relation?: string; source?: { $role?: string }; target?: { $role?: string } }
interface TransitionDef { id: string; from?: string; to?: string; actions?: ActionDef[] }
interface Index {
  locale: string;
  count: number;
  steps: StepRec[]; stepsByCut: Map<number, StepRec[]>; stepByKey: Map<string, StepRec>;
  byEventSchema: Map<string, StepRec[]>; byCommand: Map<string, StepRec[]>;
  messages: Map<string, { cut: number; message: Message }>; receipts: Map<string, { status: string; cut: number }>;
  parents: Map<string, string[]>; children: Map<string, string[]>;
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
function build(store: TemporalFeedStore, locale = 'en-US'): Index {
  const old = cache.get(store);
  if (old && old.count === store.commits.length) return { ...old, locale };
  const index: Index = {
    locale,
    count: store.commits.length, steps: [], stepsByCut: new Map(), stepByKey: new Map(), byEventSchema: new Map(), byCommand: new Map(),
    messages: new Map(), receipts: new Map(), evals: [], chains: new Map(), predicates: new Map(),
    parents: new Map(), children: new Map(),
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
  const link = (parent: string, child: string) => {
    const before = index.messages.get(parent), after = index.messages.get(child);
    if (!before || !after || parent === child || before.cut > after.cut) return;
    const parents = index.parents.get(child) ?? [], children = index.children.get(parent) ?? [];
    if (!parents.includes(parent)) parents.push(parent);
    if (!children.includes(child)) children.push(child);
    index.parents.set(child, parents); index.children.set(parent, children);
  };
  const eventKeys = new Map<string, string>();
  for (const { message } of index.messages.values()) {
    const payload = isObject(message.payload) ? message.payload : {};
    const provenance = isObject(payload.provenance) ? payload.provenance : {};
    if (typeof provenance.event_key === 'string') eventKeys.set(provenance.event_key, message.id);
  }
  for (const { message } of index.messages.values()) {
    const payload = isObject(message.payload) ? message.payload : {};
    const provenance = isObject(payload.provenance) ? payload.provenance : {};
    if (Array.isArray(provenance.message_ids)) for (const parent of provenance.message_ids) if (typeof parent === 'string') link(parent, message.id);
    if (Array.isArray(provenance.parent_event_keys)) for (const key of provenance.parent_event_keys) {
      if (typeof key !== 'string') continue;
      const parent = eventKeys.get(key); if (parent) link(parent, message.id);
    }
  }
  // Injection execution receipts identify the command and authored point.
  // Match the emitted event to that point's schema and exact submitted payload.
  // This also works for existing lean journals without kernel cause arrays.
  const points = store.header.behaviour?.injectionPoints ?? store.header.behaviour?.injection_points ?? [];
  for (const commit of store.commits) for (const receipt of commit.receipts) {
    if (receipt.status !== 'succeeded' || !isObject(receipt.result)) continue;
    const command = index.messages.get(receipt.commandId)?.message;
    if (!command || command.schemaId !== 'aas.runtime.inject_event' || !isObject(command.payload)) continue;
    const submitted = command.payload;
    const result = receipt.result;
    const point = points.find(row => row.id === result.injection_point);
    if (!point) continue;
    for (const message of commit.messages) if (message.kind === 'event' && message.source === command.target && message.schemaId === point.emits && exactValue(message.payload) === exactValue(submitted.payload)) link(command.id, message.id);
  }
  cache.set(store, index);
  return index;
}

export const idLabel = (store: TemporalFeedStore, id: string, locale = 'en-US') => {
  const entity = store.entities.get(id);
  return entity ? displayName(entity, store.header, locale) : readableLabel(id.replace(/:\d+$/, ''), locale);
};
const keyLabel = (store: TemporalFeedStore, key: EntityKey, locale = 'en-US') => idLabel(store, entityId(key), locale);
/** Short plain name for a field: last path segment without its unit suffix. */
export function fieldWords(fieldId: string, locale = 'en-US'): string {
  const business = businessFields[fieldId];
  if (business) return locale === 'zh-CN' ? business.zh : business.en;
  const last = fieldId.slice(fieldId.lastIndexOf('.') + 1).replace(/_(?:enu_m|mps2|mps|deg|rad|ns|m|s|j|w)$/, '');
  return translate(locale, last.replace(/_/g, ' '));
}
const isInstant = (value: unknown): value is { ns: unknown; microstep: unknown } => isObject(value) && 'ns' in value && 'microstep' in value && Object.keys(value).length === 2;
export function showValue(store: TemporalFeedStore, value: unknown, max = 60, locale = 'en-US'): string {
  const raw = isObject(value) && typeof value.$number === 'string' ? Number(value.$number) : value;
  if (isInstant(raw)) { const ns = isObject(raw.ns) ? raw.ns.$integer : raw.ns; return translate(locale, 'at {at}', {at:displayTime(String(ns), store.header.start.ns)}); }
  // Keep enough precision to explain strict thresholds (e.g. 0.8496596 <
  // 0.85); the compact state panel's two decimals can hide the difference.
  if (typeof raw === 'number' && Number.isFinite(raw)) return new Intl.NumberFormat(locale, { maximumSignificantDigits: 7 }).format(raw);
  const plain = stateValue(raw, locale);
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
export function writerName(producer: string, locale = 'en-US'): string {
  if (producer === 'behaviour') return translate(locale, 'event chains');
  if (producer === 'unknown') return translate(locale, 'unknown writer');
  if (producer === 'engine') return translate(locale, 'an engine');
  return translate(locale, '{name} engine', {name:readableLabel(producer.replace(/^sampled:.*/, 'sampled input'),locale)});
}

/** Plain-words rendering of a recorded predicate expression. */
export function describeExpression(node: unknown, roles: (role: string) => string, parameters: Record<string, unknown> = {}, nested = false, locale = 'en-US'): string {
  const t = translator(locale);
  if (!isObject(node)) return String(node);
  if ('field' in node) {
    const path = Array.isArray(node.path) && node.path.length ? ` [${node.path.join(',')}]` : '';
    const field = `${fieldWords(String(node.field),locale)}${path}`;
    return typeof node.role === 'string' ? t("{entity}'s {field}", {entity:roles(node.role),field}) : field;
  }
  if ('literal' in node) return typeof node.literal === 'boolean' ? t(String(node.literal)) : exactValue(node.literal);
  if ('parameter' in node) { const name = String(node.parameter); return name in parameters ? `${t(name.replace(/_/g, ' '))} (${exactValue(parameters[name])})` : name; }
  if ('var' in node) return String(node.var);
  const args = Array.isArray(node.args) ? node.args : [];
  const part = (child: unknown) => describeExpression(child, roles, parameters, true, locale);
  const infix: Record<string, string> = { eq: '=', ne: '≠', lt: '<', lte: '≤', gt: '>', gte: '≥', add: '+', sub: '−', mul: '×', div: '÷' };
  const op = String(node.op);
  if (op === 'and' || op === 'or') { const body = args.map(part).join(op === 'and' ? t(' AND ') : t(' OR ')); return nested ? `(${body})` : body; }
  if (op === 'not') return t('NOT {value}', {value:part(args[0])});
  if (op in infix && args.length === 2) { const body = `${part(args[0])} ${infix[op]} ${part(args[1])}`; return nested && !['eq', 'ne', 'lt', 'lte', 'gt', 'gte'].includes(op) ? `(${body})` : body; }
  if (op === 'distance') return t('distance between {a} and {b}', {a:part(args[0]),b:part(args[1])});
  if (op === 'len') return t('number of {value}', {value:part(args[0])});
  return `${t(op.replace(/_/g, ' '))}(${args.map(part).join(', ')})`;
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

function describeAction(store: TemporalFeedStore, step: StepRec, action: ActionDef, locale: string): string {
  const roles = rolesOf(step.rec), t = translator(locale);
  const who = (ref?: { $role?: string; $id?: string }) => { const role = ref?.$role ?? ref?.$id; return role && roles[role] ? keyLabel(store, roles[role],locale) : t('an entity'); };
  switch (action.kind) {
    case 'set': return t('Update state: {entity} · {field}', {entity:who(action.entity),field:fieldWords(action.field ?? '',locale)});
    case 'emit': return t('Announce "{event}"', {event:readableLabel(action.schema ?? action.id,locale)});
    case 'command': {
      const payload = isObject(action.payload) ? action.payload : {};
      const actor = (isObject(payload.actor) ? payload.actor : isObject(payload.entity) ? payload.entity : isObject(payload.vehicle) ? payload.vehicle : undefined) as { $role?: string; $id?: string } | undefined;
      return t('Ask {entity} to "{command}"', {entity:who(actor),command:readableLabel(action.capability ?? action.schema ?? action.id,locale)});
    }
    case 'delay': return t('Start a timer "{timer}" ({seconds} s)', {timer:action.id,seconds:Number(action.duration_ns ?? 0) / 1e9});
    case 'assert_relation': return t('Link {source} → {target} ({relation})', {source:who(action.source),target:who(action.target),relation:readableLabel(action.relation ?? 'relation',locale)});
    case 'close_relation': return t('Remove a link between entities');
    case 'complete': return t('Finish this chain');
    default: return readableLabel(action.kind,locale);
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
    instanceId: step.rec.instanceId, revision: revision(step.rec.revision), chainLabel: readableLabel(step.rec.templateId,index.locale), chainId: step.rec.templateId,
    stepId: step.rec.transitionId ?? step.rec.lifecycle, fromState, toState: step.rec.state, lifecycle: step.rec.lifecycle,
    actions: (definition?.actions ?? []).map(action => ({ id: action.id, kind: action.kind, text: describeAction(store, step, action,index.locale) })),
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
  // Without the transition definition these are same-cut producer/subject facts, not declared writes.
  const basis: LinkBasis | undefined = definition ? undefined : 'same-cut';
  return [...latest.values()].map(fact => effectView(store, fact, step.cut,undefined,index.locale,basis));
}
function effectView(store: TemporalFeedStore, fact: Fact, cut: number, at?: Instant, locale = 'en-US', basis?: LinkBasis): EffectView {
  const previous = factBefore(store, fact.entity, fact.fieldId, cut), declared = declaredWriter(store, fact.fieldId);
  const field = fieldInfo(store, fact.fieldId);
  const unit = !businessFields[fact.fieldId]?.clock && field?.unit && field.unit !== '1' ? field.unit : undefined;
  const value = (raw: unknown) => {
    if (businessFields[fact.fieldId]?.clock) {
      const stamp = typeof raw === 'number' && Number.isSafeInteger(raw) ? String(raw)
        : isObject(raw) && typeof raw.$integer === 'string' && /^-?\d+$/.test(raw.$integer) ? raw.$integer : undefined;
      if (stamp !== undefined) return displayTime(stamp, store.header.start.ns);
    }
    return showValue(store, raw, 60, locale);
  };
  return {
    entity: fact.entity, entityLabel: keyLabel(store, fact.entity,locale), fieldId: fact.fieldId, fieldLabel: fieldWords(fact.fieldId,locale),
    before: previous ? value(previous.value) : undefined, hadBefore: !!previous, after: value(fact.value),
    writer: fact.producer, declaredWriter: declared, writerOk: !declared || declared === fact.producer, at: at ?? fact.validFrom ?? store.commits.find(row => row.commitIndex === cut)!.at, cut, unit,
    ...(basis === undefined ? {} : { basis }),
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
    rows.push({ entity: key, entityLabel: keyLabel(store, key,index.locale), fieldId, fieldLabel: fieldWords(fieldId,index.locale), value: fact ? showValue(store, fact.value,60,index.locale) : translate(index.locale,'not recorded'), writer: producer, writtenAt: fact?.validFrom, ref });
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
  const roleName = (role: string) => { const key = refKey(roles[role]); return key ? keyLabel(store, key,index.locale) : readableLabel(role,index.locale); };
  return {
    predicateId: base.predicateId, label: readableLabel(base.predicateId,index.locale), edge: base.edge, value: base.value, at: base.at, readCut: base.readCut,
    expression: definition?.expression ? describeExpression(definition.expression, roleName, definition.parameters ?? {},false,index.locale) : '',
    reads: predicateReads(store, index, base.evaluation), ref: base.ref, technical: base.evaluation,
  };
}
const evalPredicateView = (store: TemporalFeedStore, index: Index, rec: PredicateTruth, cut: number) =>
  predicateView(store, index, { predicateId: rec.predicateId, value: rec.value, at: rec.evaluatedAt, readCut: rec.readCut.index, evaluation: rec, ref: { kind: 'predicate', contextId: rec.contextId, cut } });

function triggerOf(store: TemporalFeedStore, index: Index, step: StepRec): TriggerView {
  const trigger = isObject(step.rec.trigger) ? step.rec.trigger : {}, locale = index.locale, t = translator(locale);
  if (typeof trigger.predicate === 'string' && isObject(trigger.evaluation)) {
    const evaluation = trigger.evaluation as { evaluatedAt?: Instant; readCut?: { index: number; at: Instant }; roles?: unknown; acquired?: unknown; contextId?: string };
    const when = evaluation.evaluatedAt ?? step.commit.at, edge = text(trigger.edge);
    const view = predicateView(store, index, { predicateId: trigger.predicate, edge, value: true, at: when, readCut: evaluation.readCut?.index ?? step.cut, evaluation, ref: evaluation.contextId ? { kind: 'predicate', contextId: evaluation.contextId, cut: step.cut } : undefined });
    return { kind: 'predicate', summary: t('The condition "{condition}" became true', {condition:view.label}), at: when, predicate: view, ref: view.ref };
  }
  if (typeof trigger.event === 'string') {
    const found = findSender(store, index, trigger.event, trigger.payload, step.cut, text(trigger.message_id));
    const event = readableLabel(trigger.event, locale);
    return { kind: 'event', summary: locale === 'zh-CN' ? `已记录的事件触发条件：“${event}”` : `Recorded event trigger: "${event}"`, at: found?.message ? found.message.message.at : step.commit.at, source: found?.text, ref: found?.ref, basis: 'recorded' };
  }
  if (typeof trigger.timer === 'string') {
    const ref: CauseRef | undefined = typeof trigger.instance === 'string' && trigger.revision !== undefined && index.stepByKey.has(stepKey(trigger.instance, trigger.revision)) ? { kind: 'step', instanceId: trigger.instance, revision: revision(trigger.revision) } : undefined;
    return { kind: 'timer', summary: t('The timer "{timer}" ran out', {timer:trigger.timer}), at: step.commit.at, source: ref ? t('The timer was started by an earlier step of this chain') : undefined, ref };
  }
  if (typeof trigger.command_id === 'string') {
    const sent = index.messages.get(trigger.command_id), status = text(trigger.status);
    const sender = sent ? (index.stepsByCut.get(sent.cut) ?? []).find(row => row.rec.instanceId === step.rec.instanceId) : undefined;
    const command = readableLabel(text(trigger.receipt) ?? 'command',locale);
    const terminal = status !== undefined && ['succeeded', 'failed', 'rejected', 'canceled'].includes(status);
    const summary = !status ? t('The command "{command}" reported back', { command })
      : terminal ? t('The command "{command}" finished: {status}', { command, status: readableLabel(status, locale) })
        : locale === 'zh-CN' ? `指令“${command}”报告状态：${readableLabel(status, locale)}` : `The command "${command}" reported status: ${readableLabel(status, locale)}`;
    return { kind: 'command', summary, at: step.commit.at, source: sent ? t('Sent to {writer}', {writer:sent.message.target ? writerName(String(sent.message.target),locale) : t('an engine')}) : undefined, ref: sender ? { kind: 'step', instanceId: sender.rec.instanceId, revision: revision(sender.rec.revision) } : undefined, ...(sent ? { basis: 'recorded' as const } : {}) };
  }
  if (trigger.deadline !== undefined) return { kind: 'deadline', summary: t('A time limit ran out'), at: step.commit.at };
  if (trigger.instance === 'activated') return { kind: 'start', summary: t('The chain was just activated'), at: step.commit.at };
  return { kind: 'other', summary: t('Triggered by a recorded condition'), at: step.commit.at };
}
function findSender(store: TemporalFeedStore, index: Index, schema: string, payload: unknown, before: number, messageId?: string): { text: string; ref?: CauseRef; basis?: LinkBasis; message?: { cut: number; message: Message } } | undefined {
  const wanted = exactValue(payload);
  let best: { cut: number; message: Message } | undefined;
  for (const [, row] of index.messages) if (row.cut <= before && row.message.kind === 'event' && row.message.schemaId === schema && (messageId === undefined ? exactValue(row.message.payload) === wanted : row.message.id === messageId) && (!best || row.cut > best.cut)) best = row;
  if (!best) return undefined;
  const sources = best.message.source;
  if (sources === 'behaviour') {
    const origin = (index.stepsByCut.get(best.cut) ?? []).find(candidate => producedMessages(candidate, chainDef(index, candidate.rec.templateId, candidate.rec.transitionId), 'event').some(item => item.message.id === best!.message.id));
    if (origin) return { text: matchingChainStep(origin.rec.templateId, index.locale), ref: { kind: 'step', instanceId: origin.rec.instanceId, revision: revision(origin.rec.revision) }, basis: 'recorded', message: best };
  }
  void store;
  return { text: translate(index.locale,'Sent by the {writer}', {writer:writerName(sources,index.locale)}), ref: { kind: 'message', messageId: best.message.id, cut: best.cut }, basis: 'recorded', message: best };
}

function evalsByEntity(index: Index): Map<string, EvalRec[]> {
  if (index.evalsByEntity) return index.evalsByEntity;
  const map = new Map<string, EvalRec[]>();
  for (const row of index.evals) for (const key of Object.values(row.rec.roles)) { const id = entityId(key); const rows = map.get(id); if (rows) rows.push(row); else map.set(id, [row]); }
  index.evalsByEntity = map; return map;
}

/** Payload equality correlates occurrences; only an explicit id narrows the occurrence. */
function matchingEventSteps(index: Index, message: Message, cut: number): StepRec[] {
  return (index.byEventSchema.get(message.schemaId) ?? []).filter(row => {
    if (row.cut < cut) return false;
    const trigger = isObject(row.rec.trigger) ? row.rec.trigger : {};
    return typeof trigger.message_id === 'string' ? trigger.message_id === message.id : exactValue(trigger.payload) === exactValue(message.payload);
  });
}
const matchingChainStep = (templateId: string, locale: string) => locale === 'zh-CN'
  ? `匹配的已记录事件链步骤：“${readableLabel(templateId, locale)}”` : `Matching recorded chain step: "${readableLabel(templateId, locale)}"`;
const matchingTrigger = (locale: string) => locale === 'zh-CN' ? '匹配的已记录触发条件' : 'matching recorded trigger';
const matchingTriggerCount = (count: number, locale: string) => locale === 'zh-CN'
  ? `${count} 个匹配的已记录触发条件` : `${count} matching recorded trigger${count === 1 ? '' : 's'}`;

function followUpsOfStep(store: TemporalFeedStore, index: Index, step: StepRec, effects: EffectView[]): FollowUpView[] {
  const definition = chainDef(index, step.rec.templateId, step.rec.transitionId), result: FollowUpView[] = [], locale = index.locale, t = translator(locale);
  for (const item of producedMessages(step, definition, 'event')) {
    const message = item.message;
    const receivers = matchingEventSteps(index, message, step.cut);
    result.push({ kind: 'event', label: t('Announced "{event}"', {event:readableLabel(message.schemaId,locale)}), detail: matchingTriggerCount(receivers.length, locale), at: message.at, ref: { kind: 'message', messageId: message.id, cut: step.cut }, basis: 'recorded' });
    for (const receiver of receivers.slice(0, 4)) result.push(stepFollowUp(store, index, receiver, matchingTrigger(locale), 'recorded'));
  }
  for (const item of producedMessages(step, definition, 'command')) {
    const message = item.message, receipt = index.receipts.get(message.id);
    result.push({ kind: 'command', label: t('Asked {writer} to "{command}"', {writer:message.target ? writerName(String(message.target),locale) : t('an engine'),command:readableLabel(message.schemaId,locale)}), detail: receipt ? t('Result: {result}', {result:readableLabel(receipt.status,locale)}) : t('No result recorded yet'), at: message.at, ref: { kind: 'message', messageId: message.id, cut: step.cut }, basis: 'recorded' });
    for (const receiver of index.byCommand.get(message.id) ?? []) if (receiver.cut >= step.cut && receiver.key !== step.key) result.push(stepFollowUp(store, index, receiver, 'handled the result', 'recorded'));
  }
  const next = index.steps.find(row => row.rec.instanceId === step.rec.instanceId && row.cut > step.cut && row.key !== step.key);
  if (next && !result.some(row => row.ref?.kind === 'step' && row.ref.instanceId === next.rec.instanceId && revision(row.ref.revision) === revision(next.rec.revision))) result.push(stepFollowUp(store, index, next, 'continued', 'recorded'));
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
      const others = Object.values(row.rec.roles).filter(key => entityId(key) !== entityId(effect.entity)).slice(0, 2).map(key => keyLabel(store, key,locale)).join(', ');
      result.push({ kind: 'predicate', label: t('The condition "{condition}"{others} was re-checked using the new {field}', {condition:readableLabel(row.rec.predicateId,locale),others:others ? ` (${others})` : '',field:effect.fieldLabel}), detail: row.rec.value === null ? t('Result unknown') : t('Result: {result}', {result:row.rec.value ? t('true') : t('false')}), at: row.rec.evaluatedAt, ref: { kind: 'predicate', contextId: row.rec.contextId, cut: row.cut }, basis: 'recorded' });
      if (seen.size >= 4) break;
    }
  }
  return result.slice(0, 16);
}
function stepFollowUp(store: TemporalFeedStore, index: Index, step: StepRec, verb: string, basis?: LinkBasis): FollowUpView {
  const view = stepView(store, index, step);
  return { kind: 'step', label: `${view.chainLabel}: ${stepSentence(view,index.locale)}`, detail: `${translate(index.locale,verb)} · ${at(store, step.commit.at)}`, at: step.commit.at, ref: { kind: 'step', instanceId: step.rec.instanceId, revision: revision(step.rec.revision) }, ...(basis === undefined ? {} : { basis }) };
}
export const stepSentence = (view: Pick<StepView, 'fromState' | 'toState' | 'stepId'>, locale = 'en-US') => view.fromState && view.fromState !== view.toState ? `${readableLabel(view.fromState,locale)} → ${readableLabel(view.toState,locale)}` : translate(locale,'step "{step}" → {state}', {step:view.stepId,state:readableLabel(view.toState,locale)});

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
  const view = stepView(store, index, step), trigger = triggerOf(store, index, step), effects = effectsOf(store, index, step), t = translator(index.locale);
  const model: TraceModel = {
    focus: 'step', heading: `${view.chainLabel}: ${stepSentence(view,index.locale)}`, subtitle: t('at {at}', {at:at(store, step.commit.at)}), at: step.commit.at, cut: step.cut,
    trigger, step: view, effects, followUps: followUpsOfStep(store, index, step, effects), involved: [], choices: [], notes: [t(LEAN_NOTE)],
  };
  if (!chainDef(index,step.rec.templateId,step.rec.transitionId)) model.notes.unshift(t('This run did not include the chain definition, so individual actions are not listed.'));
  else if (!view.actions.length) model.notes.unshift(t('This step has no declared actions.'));
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
    if (isObject(trigger.evaluation) && trigger.evaluation.contextId === contextId) followUps.push(stepFollowUp(store, index, step, index.locale === 'zh-CN' ? '匹配的已记录求值' : 'matching recorded evaluation', 'recorded'));
    if (followUps.length >= 6) break;
  }
  const t = translator(index.locale);
  if (!followUps.length) followUps.push({ kind: 'none', label: index.locale === 'zh-CN' ? '没有匹配的已记录步骤' : 'No matching recorded steps', detail: index.locale === 'zh-CN' ? '未找到匹配此求值上下文的已记录触发条件。' : 'No recorded trigger matched this evaluation context.' });
  const model: TraceModel = {
    focus: 'predicate', heading: t('"{condition}" is {value}', {condition:view.label,value:view.value === null ? t('unknown') : view.value ? t('true') : t('false')}), subtitle: t('checked at {at}', {at:at(store, view.at)}), at: view.at, cut: row.commit.commitIndex,
    predicate: view, effects: [], followUps, involved: [], choices: [], notes: [t(LEAN_NOTE)],
  };
  model.involved = involvedOf(store, model);
  return model;
}

function explainMessage(store: TemporalFeedStore, index: Index, messageId: string): TraceModel | undefined {
  const found = index.messages.get(messageId); if (!found) return undefined;
  const { message, cut } = found, followUps: FollowUpView[] = [];
  const origin = message.source === 'behaviour' ? (index.stepsByCut.get(cut) ?? []).find(step => producedMessages(step, chainDef(index, step.rec.templateId, step.rec.transitionId), message.kind).some(item => item.message.id === message.id)) : undefined;
  const receivers = message.kind === 'event' ? matchingEventSteps(index, message, cut) : index.byCommand.get(message.id) ?? [];
  for (const receiver of receivers.slice(0, 6)) followUps.push(stepFollowUp(store, index, receiver, matchingTrigger(index.locale), 'recorded'));
  const locale = index.locale, t = translator(locale);
  // Traverse only recorded message handles, never entity/time proximity.
  const visited = new Set([message.id]), queue = [...(index.children.get(message.id) ?? [])];
  while (queue.length) {
    const id = queue.shift()!; if (visited.has(id)) continue; visited.add(id);
    const child = index.messages.get(id); if (!child) continue;
    followUps.push({ kind: child.message.kind, label: messageLabel(child.message.schemaId, locale),
      detail: messageDetail(store, child.message, locale), at: child.message.at,
      ref: { kind: 'message', messageId: id, cut: child.cut }, basis: 'recorded' });
    queue.push(...(index.children.get(id) ?? []));
  }
  followUps.sort((a, b) => a.at && b.at ? BigInt(a.at.ns) < BigInt(b.at.ns) ? -1 : BigInt(a.at.ns) > BigInt(b.at.ns) ? 1 : a.at.microstep - b.at.microstep : 0);
  if (!followUps.length) followUps.push({ kind: 'none', label: locale === 'zh-CN' ? '没有匹配的已记录后续步骤' : 'No matching recorded follow-ups', detail: locale === 'zh-CN' ? '未找到匹配的已记录触发条件或消息关联。' : 'No matching recorded trigger or message link was found.' });
  const parents = (index.parents.get(message.id) ?? []).map(id => index.messages.get(id)).filter((row): row is { cut: number; message: Message } => !!row);
  const effects = store.commits.find(row => row.commitIndex === cut)?.facts.filter(fact => fact.producer === message.source && (message.subjects ?? []).some(key => entityId(key) === entityId(fact.entity))).map(fact => effectView(store, fact, cut, undefined, locale, 'same-cut')) ?? [];
  const model: TraceModel = {
    focus: 'message', heading: messageLabel(message.schemaId,locale), subtitle: message.kind === 'event' ? t('announced at {at}', {at:at(store, message.at)}) : t('requested at {at}', {at:at(store, message.at)}), at: message.at, cut,
    sender: origin ? { text: matchingChainStep(origin.rec.templateId, locale), ref: { kind: 'step', instanceId: origin.rec.instanceId, revision: revision(origin.rec.revision) }, basis: 'recorded' } : { text: t('Sent by the {writer}', {writer:writerName(message.source,locale)}) },
    trigger: parents.length ? { kind: parents[0].message.kind, summary: messageLabel(parents[0].message.schemaId, locale), at: parents[0].message.at, ref: { kind: 'message', messageId: parents[0].message.id, cut: parents[0].cut }, basis: 'recorded' } : undefined,
    effects, followUps, involved: [], choices: parents.slice(1).map(parent => ({ label: messageLabel(parent.message.schemaId, locale), at: parent.message.at, ref: { kind: 'message', messageId: parent.message.id, cut: parent.cut }, basis: 'recorded' as const })), notes: [t(LEAN_NOTE)],
  };
  if (message.schemaId === 'lowalt.operator.rejected' && isObject(message.payload) && typeof message.payload.reason === 'string') model.notes.unshift(message.payload.reason);
  model.involved = involvedOf(store, model, undefined, message.subjects ?? []);
  return model;
}

function messageDetail(store: TemporalFeedStore, message: Message, locale: string): string {
  const names = (message.subjects ?? []).map(key => keyLabel(store, key, locale));
  if (message.schemaId === 'lowalt.operator.rejected' && isObject(message.payload) && typeof message.payload.reason === 'string') names.push(message.payload.reason);
  const receipt = message.kind === 'command' ? build(store, locale).receipts.get(message.id) : undefined;
  return [...names, ...(receipt ? [translate(locale, 'Result: {result}', { result: readableLabel(receipt.status, locale) })] : [])].join(' · ');
}

function explainChange(store: TemporalFeedStore, index: Index, entity: EntityKey, fieldId: string, cut: number): TraceModel | undefined {
  const commit = store.commits.find(row => row.commitIndex === cut); if (!commit) return undefined;
  const writing = (index.stepsByCut.get(cut) ?? []).find(step => effectsOf(store, index, step).some(effect => effect.fieldId === fieldId && entityId(effect.entity) === entityId(entity)));
  if (writing) return explainStep(store, index, writing);
  const fact = [...commit.facts].reverse().find(row => row.fieldId === fieldId && entityId(row.entity) === entityId(entity));
  if (!fact) return undefined;
  const locale = index.locale, t = translator(locale), effect = effectView(store, fact, cut, commit.at,locale), followUps: FollowUpView[] = [];
  for (const row of evalsByEntity(index).get(entityId(entity)) ?? []) {
    if (row.cut <= cut) continue;
    const used = Object.entries(row.rec.acquired ?? {}).some(([slot, stamp]) => slot.slice(slot.indexOf('.') + 1) === fieldId && String(stamp.numerator) === effect.at.ns);
    if (used) followUps.push({ kind: 'predicate', label: t('The condition "{condition}" was checked using it', {condition:readableLabel(row.rec.predicateId,locale)}), detail: row.rec.value === null ? t('Result unknown') : t('Result: {result}', {result:row.rec.value ? t('true') : t('false')}), at: row.rec.evaluatedAt, ref: { kind: 'predicate', contextId: row.rec.contextId, cut: row.cut }, basis: 'recorded' });
    if (followUps.length >= 4) break;
  }
  const model: TraceModel = {
    focus: 'change', heading: t('{entity}: {field} changed', {entity:keyLabel(store, entity,locale),field:effect.fieldLabel}), subtitle: t('at {at}', {at:at(store, commit.at)}), at: commit.at, cut,
    sender: { text: t('Written by the {writer}, not by an event chain', {writer:writerName(fact.producer,locale)}) }, effects: [effect], followUps, involved: [], choices: [], notes: [t(LEAN_NOTE)],
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
export function explain(store: TemporalFeedStore, ref: CauseRef, locale = 'en-US'): TraceModel | undefined {
  const index = build(store,locale), t = translator(locale);
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
      }
      if (cut === undefined) return undefined;
      const commit = store.commits.find(row => row.commitIndex === cut); if (!commit) return undefined;
      const event = (commit.events ?? []).find(row => ref.kind === 'cut' || ((!ref.atNs || row.at_ns === ref.atNs) && (!ref.text || row.text === ref.text)));
      entities ??= event?.entities;
      const steps = momentSteps(store, index, cut, entities);
      const ids = entities?.length ? new Set(entities) : undefined;
      const messages = commit.messages.filter(message => !message.schemaId.startsWith('aas.behaviour.') && (!ids || (message.subjects ?? []).some(key => ids.has(key.id))));
      // No business-summary message identity is recorded in this contract. Every
      // proximity candidate, including the first/only one, stays contextual.
      const choices: TraceModel['choices'] = [
        ...steps.map(step => {
          const view = stepView(store, index, step);
          return { label: `${view.chainLabel}: ${stepSentence(view, locale)}`, ref: { kind: 'step', instanceId: step.rec.instanceId, revision: revision(step.rec.revision) } as CauseRef, at: step.commit.at, basis: 'same-cut' as const };
        }),
        ...messages.map(message => ({ label: messageLabel(message.schemaId, locale), ref: { kind: 'message', messageId: message.id, cut } as CauseRef, at: message.at, basis: 'same-cut' as const })),
      ].slice(0, 8);
      const namedIds = new Set(entities), names = new Map([...store.entities.values()].map(entity => [entity.key.id, displayName(entity, store.header, locale)]));
      const contextNote = locale === 'zh-CN' ? '这些记录仅提供同一时刻的上下文；没有已记录的因果关联可将其作为此事件的解释。' : 'These records are same-cut context, not a recorded causal explanation of this event.';
      return {
        focus: 'moment', heading: event ? eventNarrative(event, locale, names) : locale === 'zh-CN' ? '此日志位置的已记录活动' : 'Recorded activity at this cut',
        subtitle: t('at {at}', { at: event ? displayTime(event.at_ns, store.header.start.ns) : at(store, commit.at) }),
        at: commit.at, cut, effects: [], followUps: [], choices,
        involved: [...store.entities.values()].filter(entity => namedIds.has(entity.key.id)).map(entity => entity.key), eventEntities: entities,
        notes: [choices.length ? contextNote : event
          ? locale === 'zh-CN' ? '此业务事件已记录；这条日志没有关联的事件链步骤或类型化消息。可在对象的“经历”页查看记录的状态变化。' : 'This business event is recorded. Its journal entry has no linked chain step or typed message. Open the object’s Story tab to inspect its recorded state changes.'
          : locale === 'zh-CN' ? '此日志位置没有记录与这些对象相关的事件链步骤或类型化消息。' : 'No chain steps or typed messages for these subjects were recorded at this cut.'],
      };
    }
  }
}

export interface Moment { key: string; label: string; detail: string; at: Instant; cut: number; ref: CauseRef; kind: 'event' | 'step' | 'condition' }
/** Notable moments involving one entity: business events, chain steps and conditions that became true. */
export function momentsFor(store: TemporalFeedStore, key: EntityKey, locale = 'en-US'): Moment[] {
  const index = build(store,locale), id = key.id, result: Moment[] = [], t = translator(locale);
  for (const commit of store.commits) {
    for (const [ordinal, event] of (commit.events ?? []).entries()) if (event.entities.includes(id)) {
      const label = locale.startsWith('zh') ? (event as typeof event & {text_zh?:string}).text_zh ?? event.text : event.text;
      result.push({ key: `e${commit.commitIndex}/${ordinal}`, label: label.length > 70 ? `${label.slice(0, 69)}…` : label, detail: t('Event'), at: commit.at, cut: commit.commitIndex, kind: 'event', ref: { kind: 'event', cut: commit.commitIndex, text: event.text, atNs: event.at_ns, entities: event.entities } });
    }
  }
  for (const step of index.steps) {
    if (!Object.values(rolesOf(step.rec)).some(role => role.id === id)) continue;
    const view = stepView(store, index, step);
    if (!view.actions.length && step.rec.templateId === 'traffic.road_follow') continue;
    result.push({ key: `s${step.key}`, label: `${view.chainLabel}: ${stepSentence(view,locale)}`, detail: t('Chain step'), at: step.commit.at, cut: step.cut, kind: 'step', ref: { kind: 'step', instanceId: step.rec.instanceId, revision: revision(step.rec.revision) } });
  }
  const previous = new Map<string, boolean | null>();
  for (const row of index.evals) {
    const before = previous.get(row.rec.contextId); previous.set(row.rec.contextId, row.rec.value);
    if (row.rec.value !== true || before === true || !Object.values(row.rec.roles).some(role => role.id === id)) continue;
    result.push({ key: `p${row.rec.contextId}/${row.cut}`, label: t('"{condition}" became true', {condition:readableLabel(row.rec.predicateId,locale)}), detail: t('Condition'), at: row.rec.evaluatedAt, cut: row.cut, kind: 'condition', ref: { kind: 'predicate', contextId: row.rec.contextId, cut: row.cut } });
  }
  return result.sort((a, b) => a.cut - b.cut || a.key.localeCompare(b.key));
}

export interface ChangeRow { key: string; fieldId: string; fieldLabel: string; before?: string; after: string; retracted: boolean; at: Instant; cut: number; writer: string; cause: string; ref: CauseRef }
export interface ChangeSummary { fieldId: string; fieldLabel: string; updates: number; writer: string }
/** Field changes of one entity over time; fields that update continuously are summarised instead of listed. */
export function changesFor(store: TemporalFeedStore, key: EntityKey, limitPerField = 24, locale = 'en-US'): { rows: ChangeRow[]; continuous: ChangeSummary[] } {
  const index = build(store,locale), id = entityId(key), rows: ChangeRow[] = [], continuous: ChangeSummary[] = [], t = translator(locale);
  const known = store.viewCursor?.knownAt ?? Number.MAX_SAFE_INTEGER;
  for (const fieldId of store.fieldIdsOf(id)) {
    if (fieldId.startsWith('aas.behaviour.') || /(?:digest|hash)/i.test(fieldId)) continue;
    const versions = store.fieldVersionsOf(id, fieldId).filter(version => version.commit.commitIndex <= known);
    if (versions.length > limitPerField) { continuous.push({ fieldId, fieldLabel: fieldWords(fieldId,locale), updates: versions.length, writer: (versions[versions.length - 1].value as Fact).producer ?? 'engine' }); continue; }
    let previous: Fact | undefined;
    for (const version of versions) {
      if (version.retract) { rows.push({ key: `${fieldId}/${version.commit.commitIndex}/r`, fieldId, fieldLabel: fieldWords(fieldId,locale), before: previous ? showValue(store, previous.value,60,locale) : undefined, after: t('removed'), retracted: true, at: version.commit.at, cut: version.commit.commitIndex, writer: 'engine', cause: t('Value withdrawn'), ref: { kind: 'change', entity: key, fieldId, cut: version.commit.commitIndex } }); previous = undefined; continue; }
      const fact = version.value as Fact, cut = version.commit.commitIndex;
      const writing = fact.producer === 'behaviour' ? (index.stepsByCut.get(cut) ?? []).find(step => effectsOf(store, index, step).some(effect => effect.fieldId === fieldId && entityId(effect.entity) === id)) : undefined;
      const cause = writing ? t('{chain} (step "{step}")', {chain:readableLabel(writing.rec.templateId,locale),step:writing.rec.transitionId ?? writing.rec.lifecycle}) : writerName(fact.producer,locale);
      rows.push({ key: `${fieldId}/${cut}/${rows.length}`, fieldId, fieldLabel: fieldWords(fieldId,locale), before: previous ? showValue(store, previous.value,60,locale) : undefined, after: showValue(store, fact.value,60,locale), retracted: false, at: version.commit.at, cut, writer: fact.producer, cause, ref: { kind: 'change', entity: key, fieldId, cut } });
      previous = fact;
    }
  }
  return { rows: rows.sort((a, b) => a.cut - b.cut || a.fieldId.localeCompare(b.fieldId)), continuous };
}

/** Test hook: forget cached indexes (live runs rebuild automatically when commits arrive). */
