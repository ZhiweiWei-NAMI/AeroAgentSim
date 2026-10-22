/**
 * Canonical agent records.
 *
 * The projector retains each original `aas.langgraph.record` and appends
 * `aas.agent.record` views whose payload declares `source_message_id`. Those
 * views are projection aliases: an observation, model call, output, finished
 * or failure is not recorded twice just because a readable view exists.
 *
 * This module folds a stream of records back onto the original identities. It
 * never invents commands or receipts, and it never counts a prompt/response
 * view as a second model call. Unknown payload fields are retained verbatim so
 * a future projector extension stays inspectable instead of being rejected.
 */
import { exactValue } from '../feeds/format';

export const AGENT_RECORD_SCHEMAS = ['aas.agent.record', 'aas.langgraph.record'] as const;
export type AgentRecordSchema = typeof AGENT_RECORD_SCHEMAS[number];

export const AGENT_PHASES = [
  'observation', 'prompt', 'response', 'model_call', 'output', 'validation',
  'command', 'failure', 'finished', 'receipt',
] as const;
export type AgentPhase = typeof AGENT_PHASES[number];

const PHASE_SET = new Set<string>(AGENT_PHASES);

/** One message from a viewer feed commit, enough to identify and order it. */
export interface AgentRecordInput {
  id: string;
  schemaId: string;
  atNs: string;
  payload: unknown;
  /** Journal commit index. Defaults to 0 when a caller has no cut. */
  cut?: number;
  /** Position within the stream/cut. Defaults to the input iteration index. */
  sequence?: number;
}

export interface ObservedField { name: string; value: string; entity?: string }
export interface ProposedCall {
  callId: string; schema: string; target: string; payload: unknown;
  summary: string; summaryZh?: string;
  journalReceipts: Array<{ status: string; result: unknown }>;
}
export interface ActualReceipt { commandId: string; callId: string; status: string; result: unknown }

/** A readable view produced by the projector from another raw record. */
export interface DerivedAgentView {
  recordId: string;
  sourceMessageId: string;
  /** The view's own phase, e.g. prompt/response, not the canonical phase. */
  phase: AgentPhase;
  data: unknown;
  /** The exact alias payload, retained for future projector fields. */
  payload: unknown;
  schemaId: string;
  atNs: string;
  cut: number;
  sequence: number;
}

/** One original record, with every derived view folded onto it. */
export interface CanonicalAgentPhase {
  /** Canonical phase: prompt/response views fold into model_call. */
  phase: AgentPhase;
  simNs: string;
  atNs: string;
  data: unknown;
  /** The exact original payload, retained for raw-record inspection. */
  payload: unknown;
  /** Original message ID when observed, else the alias's declared source ID. */
  recordId: string;
  originalId?: string;
  /** Present only when at least one alias declared provenance. */
  sourceMessageId?: string;
  /** Raw IDs of every alias merged into this entry. */
  aliasIds: string[];
  /** Original/source and alias raw IDs, in order, deduplicated. */
  rawIds: string[];
  views: DerivedAgentView[];
  schemaId: string;
  cut: number;
  sequence: number;
}

export interface CanonicalDecision {
  decisionId: string;
  simNs: string;
  /** One entry per original record; aliases never append a second phase. */
  phases: CanonicalAgentPhase[];
  /** Canonical model-call-family entries (model_call plus decision-engine response). */
  modelCalls: CanonicalAgentPhase[];
  fields: ObservedField[];
  calls: ProposedCall[];
  receipts: ActualReceipt[];
  /** Typed outputs are proposals (event/command/fact), never receipts. */
  outputs: Array<Record<string, unknown>>;
  failed: boolean;
  counts: {
    records: number;
    observations: number;
    modelCalls: number;
    outputs: number;
    calls: number;
    receipts: number;
    derived: number;
  };
}

/** Backwards-compatible name used by the console page and its focused test. */
export type AgentDecision = CanonicalDecision;

export interface CanonicalTimeline {
  decisions: CanonicalDecision[];
  problems: string[];
}

export interface CanonicaliseOptions {
  /** Only entities already observed by the console get an entity link. */
  entities?: ReadonlySet<string>;
}

interface ParsedRecord {
  input: AgentRecordInput;
  inputIndex: number;
  decisionId: string;
  viewPhase: AgentPhase;
  phase: AgentPhase;
  data: unknown;
  sourceMessageId?: string;
  cut: number;
  sequence: number;
}

const objectOf = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Agent record data is not an object');
  return value as Record<string, unknown>;
};
const rowsOf = (data: unknown): unknown[] => (Array.isArray(data) ? data : [data]);
const problemText = (problem: unknown): string => problem instanceof Error ? problem.message : String(problem);

function integer(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : fallback;
}

function textField(value: unknown, key: string): string | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const candidate = (value as Record<string, unknown>)[key];
  return typeof candidate === 'string' && candidate.length > 0 ? candidate : undefined;
}

/** Provenance is accepted on the payload and on the decoded view body. */
function sourceMessageId(input: AgentRecordInput, payload: unknown, data: unknown): string | undefined {
  const declared = textField(payload, 'source_message_id') ?? textField(payload, 'sourceMessageId');
  if (declared) return declared;
  // Original graph payloads may contain a domain field with this name. Only the
  // projector's agent-record views (or an explicit source_schema marker) are aliases.
  if (input.schemaId !== 'aas.agent.record' && textField(data, 'source_schema') !== 'aas.langgraph.record') return undefined;
  return textField(data, 'source_message_id') ?? textField(data, 'sourceMessageId');
}

function parseRecord(input: AgentRecordInput, inputIndex: number): ParsedRecord {
  if (typeof input.id !== 'string' || !input.id) throw Error('Agent record lacks id');
  const record = objectOf(input.payload);
  if (typeof record.decision_id !== 'string' || !record.decision_id) throw Error('Agent record lacks decision_id');
  if (typeof record.data_json !== 'string') throw Error(`Agent record ${record.decision_id} lacks data_json string`);
  let data: unknown;
  try { data = JSON.parse(record.data_json); } catch (problem) { throw Error(`data_json for ${record.decision_id} is not JSON: ${String(problem)}`); }
  if (typeof record.phase !== 'string' || !PHASE_SET.has(record.phase)) {
    throw Error(`Agent record ${record.decision_id} has unknown phase ${JSON.stringify(record.phase)}`);
  }
  const viewPhase = record.phase as AgentPhase;
  const source = sourceMessageId(input, record, data);
  return {
    input,
    inputIndex,
    decisionId: record.decision_id,
    viewPhase,
    phase: source && (viewPhase === 'prompt' || viewPhase === 'response') ? 'model_call' : viewPhase,
    data,
    sourceMessageId: source,
    cut: integer(input.cut, 0),
    sequence: integer(input.sequence, inputIndex),
  };
}

function observationPayload(data: unknown): unknown {
  const value = objectOf(data);
  // Derived views flatten `initial.observation`; originals retain the envelope.
  if (Array.isArray(value.fields)) return value;
  if (value.initial !== undefined) {
    const initial = objectOf(value.initial);
    if (initial.observation !== undefined) return initial.observation;
  }
  return value;
}

function observedFields(data: unknown, entities?: ReadonlySet<string>): ObservedField[] {
  const observation = objectOf(observationPayload(data));
  if (!Array.isArray(observation.fields)) throw Error('Observation lacks fields array');
  const rows = observation.fields.map(objectOf);
  return rows.map(row => {
    if (typeof row.field !== 'string' || !['known', 'absent'].includes(String(row.status))) throw Error('Invalid observed field');
    const identity = objectOf(row.entity);
    if (typeof identity.id !== 'string') throw Error('Observation lacks entity identity');
    if (row.status === 'known' && !('value' in row)) throw Error('Known observation lacks value');
    return {
      name: `${identity.id}.${row.field}`,
      value: row.status === 'absent' ? 'absent at recorded cut' : exactValue(row.value),
      entity: entities?.has(identity.id) ? identity.id : undefined,
    };
  });
}

function proposedCalls(data: unknown): ProposedCall[] {
  return rowsOf(data).map(item => {
    const row = objectOf(item);
    if (typeof row.call_id !== 'string' || !row.call_id) throw Error('Command proposal lacks call_id');
    if (typeof row.schema !== 'string' || typeof row.target !== 'string' || typeof row.decision_summary !== 'string' || !('payload' in row)) throw Error('Command proposal lacks required data');
    return {
      callId: row.call_id,
      schema: row.schema,
      target: row.target,
      payload: row.payload,
      summary: row.decision_summary,
      summaryZh: typeof row.decision_summary_zh === 'string' ? row.decision_summary_zh : undefined,
      journalReceipts: [],
    };
  });
}

function actualReceipts(data: unknown): ActualReceipt[] {
  return rowsOf(data).map(item => {
    const row = objectOf(item);
    if (typeof row.command_id !== 'string' || !row.command_id) throw Error('Receipt record lacks command_id');
    if (typeof row.call_id !== 'string' || typeof row.status !== 'string') throw Error('Receipt lacks call/status');
    return {
      commandId: row.command_id,
      callId: row.call_id,
      status: row.status,
      result: 'result' in row ? row.result : undefined,
    };
  });
}

const order = (left: { cut: number; sequence: number }, right: { cut: number; sequence: number }): number =>
  left.cut - right.cut || left.sequence - right.sequence;

function preferredView(views: ParsedRecord[]): ParsedRecord {
  // A response view carries the complete recorded call; prompt is only a view.
  return views.find(row => row.viewPhase !== 'prompt') ?? views[0];
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

/** Raw identity is unique; a reconnect may repeat the same record id. */
function uniqueRows(rows: ParsedRecord[]): ParsedRecord[] {
  const seen = new Set<string>();
  return rows.filter(row => {
    if (seen.has(row.input.id)) return false;
    seen.add(row.input.id);
    return true;
  });
}

function phaseFromGroup(rows: ParsedRecord[]): CanonicalAgentPhase {
  const originals = uniqueRows(rows.filter(row => row.sourceMessageId === undefined));
  const views = uniqueRows(rows.filter(row => row.sourceMessageId !== undefined));
  const first = rows[0];
  const primary = originals[0] ?? preferredView(views);
  const recordId = originals.length > 0
    ? originals[0].input.id
    : primary.sourceMessageId ?? primary.input.id;
  const aliasIds = unique(views.map(row => row.input.id));
  return {
    phase: primary.phase,
    simNs: first.input.atNs,
    atNs: first.input.atNs,
    data: primary.data,
    payload: primary.input.payload,
    recordId,
    originalId: originals.length > 0 ? originals[0].input.id : undefined,
    sourceMessageId: views.length > 0 ? views[0].sourceMessageId : undefined,
    aliasIds,
    rawIds: unique([recordId, ...aliasIds]),
    views: views.map(view => ({
      recordId: view.input.id,
      sourceMessageId: view.sourceMessageId!,
      phase: view.viewPhase,
      data: view.data,
      payload: view.input.payload,
      schemaId: view.input.schemaId,
      atNs: view.input.atNs,
      cut: view.cut,
      sequence: view.sequence,
    })),
    schemaId: primary.input.schemaId,
    cut: first.cut,
    sequence: first.sequence,
  };
}

function buildDecision(decisionId: string, phases: CanonicalAgentPhase[], entities: ReadonlySet<string> | undefined, problems: string[]): CanonicalDecision {
  let fields: ObservedField[] = [];
  const calls: ProposedCall[] = [];
  const receipts: ActualReceipt[] = [];
  const outputs: Array<Record<string, unknown>> = [];
  let failed = false;
  for (const phase of phases) {
    try {
      if (phase.phase === 'observation') fields = observedFields(phase.data, entities);
      if (phase.phase === 'output') {
        const output = objectOf(phase.data);
        if (!['event', 'command', 'fact'].includes(String(output.kind))) throw Error('Unknown LangGraph output kind');
        outputs.push(output);
      }
      if (phase.phase === 'command') calls.push(...proposedCalls(phase.data));
      if (phase.phase === 'receipt') receipts.push(...actualReceipts(phase.data));
      if (phase.phase === 'failure') failed = true;
    } catch (problem) {
      problems.push(problemText(problem));
    }
  }
  const modelCalls = phases.filter(phase => phase.phase === 'model_call' || phase.phase === 'response');
  return {
    decisionId,
    simNs: phases[0].simNs,
    phases,
    modelCalls,
    fields,
    calls,
    receipts,
    outputs,
    failed,
    counts: {
      records: phases.length,
      observations: phases.filter(phase => phase.phase === 'observation').length,
      modelCalls: modelCalls.length,
      outputs: outputs.length,
      calls: calls.length,
      receipts: receipts.length,
      derived: phases.reduce((total, phase) => total + phase.aliasIds.length, 0),
    },
  };
}

/**
 * Fold a stream of original and derived agent records into one canonical view.
 *
 * Ordering is by journal cut then stream sequence, independent of arrival
 * order. Parsing faults become problems rather than exceptions so one bad
 * record cannot hide the rest of a decision.
 */
export function canonicaliseRecords(records: Iterable<AgentRecordInput>, options: CanonicaliseOptions = {}): CanonicalTimeline {
  const parsed: ParsedRecord[] = [];
  const problems: string[] = [];
  let index = 0;
  for (const input of records) {
    try { parsed.push(parseRecord(input, index)); } catch (problem) { problems.push(problemText(problem)); }
    index += 1;
  }
  parsed.sort((left, right) => order(left, right) || left.inputIndex - right.inputIndex);

  const groups = new Map<string, ParsedRecord[]>();
  for (const row of parsed) {
    const key = row.sourceMessageId ?? row.input.id;
    const rows = groups.get(key) ?? [];
    rows.push(row);
    groups.set(key, rows);
  }

  const byDecision = new Map<string, CanonicalAgentPhase[]>();
  for (const rows of groups.values()) {
    const decisionId = primaryDecisionId(rows);
    const phase = phaseFromGroup(rows);
    const phases = byDecision.get(decisionId) ?? [];
    phases.push(phase);
    byDecision.set(decisionId, phases);
  }

  const decisions: CanonicalDecision[] = [];
  for (const [decisionId, phases] of byDecision) {
    phases.sort((left, right) => order(left, right) || left.recordId.localeCompare(right.recordId));
    decisions.push(buildDecision(decisionId, phases, options.entities, problems));
  }
  decisions.sort((left, right) => order(left.phases[0], right.phases[0]) || left.decisionId.localeCompare(right.decisionId));
  return { decisions, problems };
}

function primaryDecisionId(rows: ParsedRecord[]): string {
  return (rows.find(row => row.sourceMessageId === undefined) ?? rows[0]).decisionId;
}

/** Alias spelling retained for callers that prefer it. */
export const canonicaliseAgentRecords = canonicaliseRecords;
export const canonicalizeRecords = canonicaliseRecords;
