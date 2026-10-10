import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Tag } from 'antd';
import { Link, useLocation } from 'react-router-dom';
import { HttpViewerFeed, RunsApi } from '../feeds/http';
import { exactValue } from '../feeds/format';
import { PageHeader, PageState } from '../console/PageState';
import { Details } from '../console/Details';
import { readableLabel } from './inspection-format';
import { displayTime } from './display-time';
import { useI18n } from '../i18n/I18nProvider';
import './agent-console.css';

const RECORD_SCHEMAS = new Set(['aas.agent.record', 'aas.langgraph.record']);
const PHASES = ['observation', 'prompt', 'response', 'model_call', 'output', 'validation', 'command', 'failure', 'finished', 'receipt'] as const;
type PhaseName = typeof PHASES[number];

interface ObservedField { name: string; value: string; entity?: string }
interface ProposedCall { callId: string; schema: string; target: string; payload: unknown; summary: string; summaryZh?: string; journalReceipts: Array<{ status: string; result: unknown }> }
interface ActualReceipt { commandId: string; callId: string; status: string; result: unknown }
export interface AgentDecision {
  decisionId: string; simNs: string;
  phases: Array<{ phase: PhaseName; simNs: string; data: unknown }>;
  fields: ObservedField[]; calls: ProposedCall[]; receipts: ActualReceipt[]; outputs: Array<Record<string,unknown>>; failed: boolean;
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

export function ingestRecord(payload: unknown, atNs: string, decisions: Map<string, AgentDecision>, entities: Set<string>, schema = 'aas.agent.record'): void {
  const record = objectOf(payload);
  if (typeof record.decision_id !== 'string' || !record.decision_id) throw Error('Agent record lacks decision_id');
  if (typeof record.data_json !== 'string') throw Error(`Agent record ${record.decision_id} lacks data_json string`);
  let data: unknown;
  try { data = JSON.parse(record.data_json); } catch (problem) { throw Error(`data_json for ${record.decision_id} is not JSON: ${String(problem)}`); }
  const phase = typeof record.phase === 'string' && (PHASES as readonly string[]).includes(record.phase) ? record.phase as PhaseName : undefined;
  if (!phase) throw Error(`Agent record ${record.decision_id} has unknown phase ${JSON.stringify(record.phase)}`);
  const decision = decisions.get(record.decision_id) ?? { decisionId: record.decision_id, simNs: atNs, phases: [], fields: [], calls: [], receipts: [], outputs: [], failed: false };
  decision.phases.push({ phase, simNs: atNs, data });
  if (phase === 'observation') decision.fields = observedFields(schema === 'aas.langgraph.record' ? objectOf(objectOf(data).initial).observation : data, entities);
  if (phase === 'output') {
    const output = objectOf(data);
    if (!['event','command','fact'].includes(String(output.kind))) throw Error('Unknown LangGraph output kind');
    decision.outputs.push(output);
  }
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
  const { t, locale } = useI18n();
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
        if (requestedCut) { const cut = Number(requestedCut); if (!Number.isSafeInteger(cut) || cut < 1) throw Error(t('Invalid decision inspection cut')); if (commit.commitIndex > cut) return; }
        for (const receipt of commit.receipts) {
          const rows = journalReceipts.get(receipt.commandId) ?? [];
          rows.push({status: receipt.status, result: receipt.result});
          journalReceipts.set(receipt.commandId, rows);
        }
        for (const item of commit.created) entities.add(item.id);
        for (const fact of commit.facts) entities.add(fact.entity.id);
        for (const edge of commit.edges) { entities.add(edge.source.id); entities.add(edge.target.id); }
        for (const message of commit.messages) {
          if (!RECORD_SCHEMAS.has(message.schemaId)) continue;
          try { ingestRecord(message.payload, message.at.ns, decisions, entities, message.schemaId); }
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
  if (!runId) return <div className="console-page"><PageHeader eyebrow={t('Inspect')} title={t('Agent decisions')} /><PageState kind="empty" title={t('Choose a run')} description={t('Open a recorded run in Inspect to browse its agent decisions.')} action={<Link className="console-btn" to={`/inspect?api=${encodeURIComponent(apiBase)}`}>{t('Browse runs')}</Link>} /></div>;
  const list = [...decisions.values()];
  const entityLink = (entity: string) => <Link to={`/runs/${encodeURIComponent(runId)}${suffix}&entity=${encodeURIComponent(entity)}`}>{readableLabel(entity,locale)}</Link>;
  const proposalLabel = (kind: unknown) => kind === 'event' ? t('Proposed event') : kind === 'command' ? t('Proposed command') : t('Proposed fact');
  return <div className="console-page agent-console-page">
    <PageHeader eyebrow={t('Inspect')} title={t('Agent decisions')} description={t('Recorded observations, proposed actions and actual execution receipts.')} />
    <p><Tag>{mode==='live'?t('Live'):t('Replay')}</Tag>{requestedCut && <Tag>{t('Selected timeline moment')}</Tag>} <Details title={t('Decision stream details')} buttonLabel={t('Details')}><pre>{JSON.stringify({runId,requestedCut},null,2)}</pre></Details> <Tag color={error ? 'red' : 'blue'}>{readableLabel(status,locale)}</Tag> <Link to={`/runs/${encodeURIComponent(runId)}${suffix}`}>{t('Run viewer')}</Link> · <Link to={`/agents/${encodeURIComponent(runId)}?api=${encodeURIComponent(apiBase)}&mode=${mode === 'live' ? 'replay' : 'live'}`}>{t('Switch feed mode')}</Link></p>
    {error && <Alert type="error" showIcon message={error} className="agent-console-alert" />}
    {problems.slice(0, 10).map((problem, index) => <Alert key={index} type="error" showIcon message={t('A decision record could not be read.')} description={<Details title={t('Record problem')} buttonLabel={t('Technical details')}><pre>{problem}</pre></Details>} className="agent-console-alert" />)}
    {problems.length > 10 && <p>{t('{count} more record problems hidden.', {count:problems.length-10})}</p>}
    {list.length === 0 && !error && <PageState kind={status === 'loading' ? 'loading' : 'empty'} title={status === 'loading' ? t('Loading decisions') : t('No decisions recorded')} description={t('Agent observations and calls appear here when recorded in this journal.')} />}
    {list.map((decision, decisionIndex) => <section key={decision.decisionId} className="agent-decision-card">
      <h3>{t('Decision {number}', {number:decisionIndex+1})} <Details title={t('Decision identity')} buttonLabel={t('Identity')}><pre>{decision.decisionId}</pre></Details> <Tag>{displayTime(decision.simNs, startNs.current)}</Tag>
        {decision.phases.map((item, index) => <Tag key={index} color={item.phase === 'failure' ? 'red' : item.phase === 'finished' ? 'green' : 'blue'}>{readableLabel(item.phase,locale)} · {displayTime(item.simNs, startNs.current)}</Tag>)}</h3>
      {decision.failed && <Alert type="error" showIcon message={t('Agent reported failure for this decision')} className="agent-console-alert" />}
      <p className="agent-record-caption"><b>{t('Observation')}</b> — {t('{count} field(s)', {count:decision.fields.length})}</p>
      <ul className="agent-field-list">
        {decision.fields.map((field, index) => <li key={index}><span>{field.name}</span> · <Details title={field.name} buttonLabel={t('Observed value')}><pre>{field.value==='absent at recorded cut'?t('absent at recorded cut'):field.value}</pre></Details>{field.entity && <> · {t('entity')} {entityLink(field.entity)}</>}</li>)}
      </ul>
      {decision.calls.map(call => <div key={call.callId} className="agent-proposed-call">
        <b>{t('Proposed call')}</b> {call.schema && <Tag>{call.schema}</Tag>}{call.target && <Tag>{t('Target: {target}', {target:call.target})}</Tag>}
        {call.summary && <div>{locale.startsWith('zh')?call.summaryZh??call.summary:call.summary}</div>}
        <Details title={t('Proposed call {id}', {id:call.callId})} buttonLabel={t('Args payload')}><Blob value={call.payload} /></Details>
        {call.journalReceipts.map((receipt, index) => <div key={index} className="agent-record-caption">{t('Committed receipt:')} <Tag>{readableLabel(receipt.status,locale)}</Tag><Details title={t('Committed receipt {status}', {status:readableLabel(receipt.status,locale)})} buttonLabel={t('Result')}><Blob value={receipt.result} /></Details></div>)}
      </div>)}
      {decision.outputs.map((output,index)=><div key={index} className="agent-proposed-call"><b>{proposalLabel(output.kind)}</b> <Tag>{String(output.schema ?? output.field)}</Tag><Details title={t('Recorded proposal')} buttonLabel={t('Proposal details')}><Blob value={output}/></Details></div>)}
      {decision.receipts.length > 0 && <div><b>{t('Actual receipts')}</b>
        <ul className="agent-field-list">{decision.receipts.map((receipt, index) => <li key={index}><span>{readableLabel(receipt.status,locale)}</span><Details title={t('Receipt identity')} buttonLabel={t('Identity')}><pre>{JSON.stringify({commandId:receipt.commandId,callId:receipt.callId},null,2)}</pre></Details>
          <Details title={t('Receipt {id}', {id:receipt.commandId})} buttonLabel={t('Result')}><Blob value={receipt.result} /></Details></li>)}</ul></div>}
      {(['observation', 'prompt', 'response', 'model_call', 'validation', 'finished', 'failure'] as const).map(phase => {
        const items = decision.phases.filter(item => item.phase === phase);
        return items.length === 0 ? null : <Details key={phase} title={readableLabel(phase,locale)} buttonLabel={`${readableLabel(phase,locale)} (${items.length})`}>
          {items.map((item, index) => <div key={index}><small>{displayTime(item.simNs, startNs.current)}</small><Blob value={item.data} /></div>)}
        </Details>;
      })}
    </section>)}
  </div>;
}
