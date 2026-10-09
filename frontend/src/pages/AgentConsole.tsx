import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Tag } from 'antd';
import { Link, useLocation } from 'react-router-dom';
import { HttpViewerFeed, RunsApi } from '../feeds/http';
import { exactValue, seconds } from '../feeds/format';
import { PageHeader, PageState } from '../console/PageState';
import { Details } from '../console/Details';
import { readableLabel } from './inspection-format';
import './agent-console.css';

const SCHEMA_ID = 'aas.agent.record';
const PHASES = ['observation', 'prompt', 'response', 'validation', 'command', 'failure', 'finished', 'receipt'] as const;
type PhaseName = typeof PHASES[number];

interface ObservedField { name: string; value: string; entity?: string }
interface ProposedCall { callId: string; schema: string; target: string; payload: unknown; summary: string; journalReceipts: Array<{ status: string; result: unknown }> }
interface ActualReceipt { commandId: string; callId: string; status: string; result: unknown }
interface AgentDecision {
  decisionId: string; simNs: string;
  phases: Array<{ phase: PhaseName; simNs: string; data: unknown }>;
  fields: ObservedField[]; calls: ProposedCall[]; receipts: ActualReceipt[]; failed: boolean;
}

const objectOf = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Agent record data is not an object');
  return value as Record<string, unknown>;
};
const rowsOf = (data: unknown): unknown[] => (Array.isArray(data) ? data : [data]);

function observedFields(data: unknown, entities: Set<string>): ObservedField[] {
  const observation = objectOf(data);
  if (!Array.isArray(observation.fields)) throw Error('Observation lacks fields array');
  const rows = observation.fields.map(objectOf);
  return rows.map(row => {
    if (typeof row.field !== 'string' || !['known', 'absent'].includes(String(row.status))) throw Error('Invalid observed field');
    const identity = objectOf(row.entity);
    if (typeof identity.id !== 'string') throw Error('Observation lacks entity identity');
    if (row.status === 'known' && !('value' in row)) throw Error('Known observation lacks value');
    return { name: `${identity.id}.${row.field}`, value: row.status === 'absent' ? 'absent at recorded cut' : exactValue(row.value), entity: entities.has(identity.id) ? identity.id : undefined };
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

function ingestRecord(payload: unknown, atNs: string, decisions: Map<string, AgentDecision>, entities: Set<string>): void {
  const record = objectOf(payload);
  if (typeof record.decision_id !== 'string' || !record.decision_id) throw Error('Agent record lacks decision_id');
  if (typeof record.data_json !== 'string') throw Error(`Agent record ${record.decision_id} lacks data_json string`);
  let data: unknown;
  try { data = JSON.parse(record.data_json); } catch (problem) { throw Error(`data_json for ${record.decision_id} is not JSON: ${String(problem)}`); }
  const phase = typeof record.phase === 'string' && (PHASES as readonly string[]).includes(record.phase) ? record.phase as PhaseName : undefined;
  if (!phase) throw Error(`Agent record ${record.decision_id} has unknown phase ${JSON.stringify(record.phase)}`);
  const decision = decisions.get(record.decision_id) ?? { decisionId: record.decision_id, simNs: atNs, phases: [], fields: [], calls: [], receipts: [], failed: false };
  decision.phases.push({ phase, simNs: atNs, data });
  if (phase === 'observation') decision.fields = observedFields(data, entities);
  if (phase === 'command') decision.calls.push(...proposedCalls(data));
  if (phase === 'receipt') decision.receipts.push(...actualReceipts(data));
  if (phase === 'failure') decision.failed = true;
  decisions.set(record.decision_id, decision);
}

function Blob({ value }: { value: unknown }) {
  return <pre className="agent-record-blob">
    {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>;
}

export default function AgentConsole() {
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const apiBase = query.get('api') ?? (['3000', '4179'].includes(window.location.port) ? 'http://127.0.0.1:8002' : window.location.origin);
  const api = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const id = location.pathname.slice('/agents/'.length);
  const runId = location.pathname.startsWith('/agents/') && id ? decodeURIComponent(id) : undefined;
  const requestedCut = query.get('cut');
  const mode = query.get('mode') === 'live' ? 'live' : 'replay';
  const scope = `${apiBase}|${runId ?? ''}|${mode}|${requestedCut ?? ''}`;
  const decisions = useMemo(() => new Map<string, AgentDecision>(), [scope]);
  const entities = useMemo(() => new Set<string>(), [scope]);
  const problems = useMemo(() => [] as string[], [scope]);
  const journalReceipts = useMemo(() => new Map<string, Array<{status: string; result: unknown}>>(), [scope]);
  const startNs = useRef('0');
  const [, redraw] = useState(0);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState<string>();
  const suffix = `?api=${encodeURIComponent(apiBase)}&mode=${mode}${requestedCut ? `&cut=${encodeURIComponent(requestedCut)}` : ''}${query.get('epoch') ? `&epoch=${encodeURIComponent(query.get('epoch')!)}` : ''}`;
  useEffect(() => {
    if (!runId) return;
    const abort = new AbortController();
    setStatus('loading'); setError(undefined); decisions.clear(); entities.clear(); journalReceipts.clear(); problems.length = 0;
    const feed = new HttpViewerFeed(api, runId, mode, setStatus);
    feed.header().then(header => {
      if (abort.signal.aborted) return;
      startNs.current = header.start.ns;
      return feed.subscribe(0, commit => {
        if (abort.signal.aborted) return;
        if (requestedCut) { const cut = Number(requestedCut); if (!Number.isSafeInteger(cut) || cut < 1) throw Error('Invalid decision inspection cut'); if (commit.commitIndex > cut) return; }
        for (const receipt of commit.receipts) {
          const rows = journalReceipts.get(receipt.commandId) ?? [];
          rows.push({status: receipt.status, result: receipt.result});
          journalReceipts.set(receipt.commandId, rows);
        }
        for (const item of commit.created) entities.add(item.id);
        for (const fact of commit.facts) entities.add(fact.entity.id);
        for (const edge of commit.edges) { entities.add(edge.source.id); entities.add(edge.target.id); }
        for (const message of commit.messages) {
          if (message.schemaId !== SCHEMA_ID) continue;
          try { ingestRecord(message.payload, message.at.ns, decisions, entities); }
          catch (problem) { problems.push(String(problem instanceof Error ? problem.message : problem)); }
        }
        for (const decision of decisions.values())
          for (const call of decision.calls) {
            const mapping = decision.receipts.find(receipt => receipt.callId === call.callId);
            if (mapping) call.journalReceipts = journalReceipts.get(mapping.commandId) ?? [];
          }
        redraw(count => count + 1);
      }, abort.signal);
    }).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    return () => abort.abort();
  }, [api, runId, mode, decisions, entities, problems, journalReceipts]);
  if (!runId) return <div className="console-page"><PageHeader eyebrow="Inspect" title="Agent decisions" /><PageState kind="empty" title="Choose a run" description="Open a recorded run in Inspect to browse its agent decisions." action={<Link className="console-btn" to={`/inspect?api=${encodeURIComponent(apiBase)}`}>Browse runs</Link>} /></div>;
  const list = [...decisions.values()];
  const entityLink = (entity: string) => <Link to={`/runs/${encodeURIComponent(runId)}${suffix}&entity=${encodeURIComponent(entity)}`}>{readableLabel(entity)}</Link>;
  return <div className="console-page agent-console-page">
    <PageHeader eyebrow="Inspect" title="Agent decisions" description="Recorded observations, proposed actions and actual execution receipts." />
    <p><Tag>{mode}</Tag>{requestedCut && <Tag>Selected timeline moment</Tag>} <Details title="Decision stream details"><pre>{JSON.stringify({runId,requestedCut},null,2)}</pre></Details> <Tag color={error ? 'red' : 'blue'}>{status}</Tag> <Link to={`/runs/${encodeURIComponent(runId)}${suffix}`}>Run viewer</Link> · <Link to={`/agents/${encodeURIComponent(runId)}?api=${encodeURIComponent(apiBase)}&mode=${mode === 'live' ? 'replay' : 'live'}`}>Switch feed mode</Link></p>
    {error && <Alert type="error" showIcon message={error} className="agent-console-alert" />}
    {problems.slice(0, 10).map((problem, index) => <Alert key={index} type="error" showIcon message={problem} className="agent-console-alert" />)}
    {problems.length > 10 && <p>{problems.length - 10} more record problems hidden.</p>}
    {list.length === 0 && !error && <PageState kind={status === 'loading' ? 'loading' : 'empty'} title={status === 'loading' ? 'Loading decisions' : 'No decisions recorded'} description="Agent observations and calls appear here when recorded in this journal." />}
    {list.map((decision, decisionIndex) => <section key={decision.decisionId} className="agent-decision-card">
      <h3>Decision {decisionIndex + 1} <Details title="Decision identity" buttonLabel="Identity"><pre>{decision.decisionId}</pre></Details> <Tag>{seconds(decision.simNs, startNs.current)}</Tag>
        {decision.phases.map((item, index) => <Tag key={index} color={item.phase === 'failure' ? 'red' : item.phase === 'finished' ? 'green' : 'blue'}>{item.phase} · {seconds(item.simNs, startNs.current)}</Tag>)}</h3>
      {decision.failed && <Alert type="error" showIcon message="Agent reported failure for this decision" className="agent-console-alert" />}
      <p className="agent-record-caption"><b>Observation</b> — {decision.fields.length} field(s)</p>
      <ul className="agent-field-list">
        {decision.fields.map((field, index) => <li key={index}><span>{readableLabel(field.name)}</span> · <Details title={field.name} buttonLabel="Observed value"><pre>{field.value}</pre></Details>{field.entity && <> · entity {entityLink(field.entity)}</>}</li>)}
      </ul>
      {decision.calls.map(call => <div key={call.callId} className="agent-proposed-call">
        <b>Proposed call</b> {call.schema && <Tag>{readableLabel(call.schema)}</Tag>}{call.target && <Tag>Target: {readableLabel(call.target)}</Tag>}
        {call.summary && <div>{call.summary}</div>}
        <Details title={`Proposed call ${call.callId}`} buttonLabel="Args payload"><Blob value={call.payload} /></Details>
        {call.journalReceipts.map((receipt, index) => <div key={index} className="agent-record-caption">Committed receipt: <Tag>{receipt.status}</Tag><Details title={`Committed receipt ${receipt.status}`} buttonLabel="result"><Blob value={receipt.result} /></Details></div>)}
      </div>)}
      {decision.receipts.length > 0 && <div><b>Actual receipts</b>
        <ul className="agent-field-list">{decision.receipts.map((receipt, index) => <li key={index}><span>{readableLabel(receipt.status)}</span><Details title="Receipt identity" buttonLabel="Identity"><pre>{JSON.stringify({commandId:receipt.commandId,callId:receipt.callId},null,2)}</pre></Details>
          <Details title={`Receipt ${receipt.commandId}`} buttonLabel="result"><Blob value={receipt.result} /></Details></li>)}</ul></div>}
      {(['observation', 'prompt', 'response', 'validation', 'finished', 'failure'] as const).map(phase => {
        const items = decision.phases.filter(item => item.phase === phase);
        return items.length === 0 ? null : <Details key={phase} title={phase} buttonLabel={`${phase} (${items.length})`}>
          {items.map((item, index) => <div key={index}><small>{seconds(item.simNs, startNs.current)}</small><Blob value={item.data} /></div>)}
        </Details>;
      })}
    </section>)}
  </div>;
}
