import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Tag } from 'antd';
import { Link, useLocation } from 'react-router-dom';
import { HttpViewerFeed, RunsApi } from '../feeds/http';
import { PageHeader, PageState } from '../console/PageState';
import { Details } from '../console/Details';
import { readableLabel } from './inspection-format';
import { displayTime } from './display-time';
import { useI18n } from '../i18n/I18nProvider';
import { AGENT_RECORD_SCHEMAS, canonicaliseRecords, type AgentDecision, type AgentRecordInput } from '../agents/records';
import './agent-console.css';

export type { AgentDecision };

const RECORD_SCHEMAS = new Set<string>(AGENT_RECORD_SCHEMAS);

/** Replace the console view with the canonical fold of every record seen so far. */
export function ingestRecords(records: readonly AgentRecordInput[], decisions: Map<string, AgentDecision>, entities: Set<string>): string[] {
  const timeline = canonicaliseRecords(records, { entities });
  decisions.clear();
  for (const decision of timeline.decisions) decisions.set(decision.decisionId, decision);
  return timeline.problems;
}

const ingestedRecords = new WeakMap<Map<string, AgentDecision>, AgentRecordInput[]>();
let inlineRecord = 0;

/**
 * Compatibility shim for callers that feed one record at a time. The console
 * itself uses `ingestRecords` with projector IDs so alias provenance can be
 * resolved; this shim keeps the existing focused test and older callers working.
 */
export function ingestRecord(payload: unknown, atNs: string, decisions: Map<string, AgentDecision>, entities: Set<string>, schema = 'aas.agent.record'): void {
  const rows = ingestedRecords.get(decisions) ?? [];
  const input: AgentRecordInput = {
    id: `inline-${inlineRecord}`,
    schemaId: schema,
    atNs,
    payload,
    cut: rows.length,
    sequence: rows.length,
  };
  inlineRecord += 1;
  const timeline = canonicaliseRecords([...rows, input], { entities });
  if (timeline.problems.length > 0) throw Error(timeline.problems[0]);
  rows.push(input);
  ingestedRecords.set(decisions, rows);
  decisions.clear();
  for (const decision of timeline.decisions) decisions.set(decision.decisionId, decision);
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
  const rawRecords = useMemo(() => [] as AgentRecordInput[], [scope]);
  const startNs = useRef('0');
  const [, redraw] = useState(0);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState<string>();
  const suffix = `?api=${encodeURIComponent(apiBase)}&mode=${mode}${requestedCut ? `&cut=${encodeURIComponent(requestedCut)}` : ''}${query.get('epoch') ? `&epoch=${encodeURIComponent(query.get('epoch')!)}` : ''}`;
  useEffect(() => {
    if (!runId) return;
    const abort = new AbortController();
    setStatus('loading'); setError(undefined); decisions.clear(); entities.clear(); journalReceipts.clear(); problems.length = 0; rawRecords.length = 0;
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
        // Keep raw records so an alias can fold onto an original across commits.
        for (const [index, message] of commit.messages.entries()) {
          if (!RECORD_SCHEMAS.has(message.schemaId)) continue;
          rawRecords.push({ id: message.id, schemaId: message.schemaId, atNs: message.at.ns, cut: commit.commitIndex, sequence: index, payload: message.payload });
        }
        const nextProblems = ingestRecords(rawRecords, decisions, entities);
        problems.length = 0; problems.push(...nextProblems);
        for (const decision of decisions.values())
          for (const call of decision.calls) {
            const mapping = decision.receipts.find(receipt => receipt.callId === call.callId);
            if (mapping) call.journalReceipts = journalReceipts.get(mapping.commandId) ?? [];
          }
        redraw(count => count + 1);
      }, abort.signal);
    }).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    return () => abort.abort();
  }, [api, runId, mode, decisions, entities, problems, journalReceipts, rawRecords]);
  if (!runId) return <div className="console-page"><PageHeader eyebrow={t('Inspect')} title={t('Agent decisions')} /><PageState kind="empty" title={t('Choose a run')} description={t('Open a recorded run in Inspect to browse its agent decisions.')} action={<Link className="console-btn" to={`/inspect?api=${encodeURIComponent(apiBase)}`}>{t('Browse runs')}</Link>} /></div>;
  const list = [...decisions.values()];
  const zh = locale.startsWith('zh');
  const entityLink = (entity: string) => <Link to={`/runs/${encodeURIComponent(runId)}${suffix}&entity=${encodeURIComponent(entity)}`}>{readableLabel(entity,locale)}</Link>;
  const proposalLabel = (kind: unknown) => kind === 'event' ? t('Proposed event') : kind === 'command' ? t('Proposed command') : t('Proposed fact');
  const derivedViewLabel = (recordId: string) => zh ? `记录 ${recordId} 的派生视图` : `Derived view of record ${recordId}`;
  return <div className="console-page agent-console-page">
    <PageHeader eyebrow={t('Inspect')} title={t('Agent decisions')} description={t('Recorded observations, proposed actions and actual execution receipts.')} />
    <p><Tag>{mode==='live'?t('Live'):t('Replay')}</Tag>{requestedCut && <Tag>{t('Selected timeline moment')}</Tag>} <Details title={t('Decision stream details')} buttonLabel={t('Details')}><pre>{JSON.stringify({runId,requestedCut},null,2)}</pre></Details> <Tag color={error ? 'red' : 'blue'}>{readableLabel(status,locale)}</Tag> <Link to={`/runs/${encodeURIComponent(runId)}${suffix}`}>{t('Run viewer')}</Link> · <Link to={`/agents/${encodeURIComponent(runId)}?api=${encodeURIComponent(apiBase)}&mode=${mode === 'live' ? 'replay' : 'live'}`}>{t('Switch feed mode')}</Link></p>
    {error && <Alert type="error" showIcon message={error} className="agent-console-alert" />}
    {problems.slice(0, 10).map((problem, index) => <Alert key={index} type="error" showIcon message={t('A decision record could not be read.')} description={<Details title={t('Record problem')} buttonLabel={t('Technical details')}><pre>{problem}</pre></Details>} className="agent-console-alert" />)}
    {problems.length > 10 && <p>{t('{count} more record problems hidden.', {count:problems.length-10})}</p>}
    {list.length === 0 && !error && <PageState kind={status === 'loading' ? 'loading' : 'empty'} title={status === 'loading' ? t('Loading decisions') : t('No decisions recorded')} description={t('Agent observations and calls appear here when recorded in this journal.')} />}
    {list.map((decision, decisionIndex) => <section key={decision.decisionId} className="agent-decision-card">
      <h3>{t('Decision {number}', {number:decisionIndex+1})} <Details title={t('Decision identity')} buttonLabel={t('Identity')}><pre>{decision.decisionId}</pre></Details> <Tag>{displayTime(decision.simNs, startNs.current)}</Tag>
        {decision.counts.modelCalls > 0 && <Tag>{zh ? `模型调用 ${decision.counts.modelCalls}` : `Model calls ${decision.counts.modelCalls}`}</Tag>}
        {decision.phases.map(item => <span key={item.recordId}>
          <Tag color={item.phase === 'failure' ? 'red' : item.phase === 'finished' ? 'green' : 'blue'}>{readableLabel(item.phase,locale)} · {displayTime(item.simNs, startNs.current)}</Tag>
          {item.aliasIds.length > 0 && <small>{' '}{derivedViewLabel(item.recordId)}{item.aliasIds.length > 1 ? ` +${item.aliasIds.length - 1}` : ''}</small>}
        </span>)}</h3>
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
          {items.map(item => <div key={item.recordId}>
            <small>{displayTime(item.simNs, startNs.current)}</small>
            <Blob value={item.data} />
            {item.aliasIds.length > 0 && <pre>{JSON.stringify({recordId:item.recordId,originalId:item.originalId ?? null,sourceMessageId:item.sourceMessageId ?? null,rawIds:item.rawIds,aliasIds:item.aliasIds},null,2)}</pre>}
            {item.views.map(view => <div key={view.recordId}>
              <small>{derivedViewLabel(view.sourceMessageId)} · {view.recordId}</small>
              <Blob value={view.data} />
            </div>)}
          </div>)}
        </Details>;
      })}
    </section>)}
  </div>;
}
