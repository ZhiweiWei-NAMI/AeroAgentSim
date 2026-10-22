import { useMemo, type ReactNode } from 'react';
import { AGENT_RECORD_SCHEMAS, canonicaliseRecords, type AgentRecordInput, type CanonicalDecision } from '../agents/records';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { useI18n } from '../i18n/I18nProvider';
import { CATEGORY_STYLE } from '../newcomers/categories';
import { EventFeed } from '../newcomers/EventFeed';
import { KpiPanel } from '../newcomers/KpiPanel';
import type { KpiSample, KpiValues } from '../newcomers/api';
import { eventNarrative } from '../newcomers/presentation';
import type { RecordedBusinessEvent } from '../newcomers/projection';
import { displayTime } from '../pages/display-time';
import { readableLabel } from '../pages/inspection-format';
import type { RunSession } from './useRunSession';

export type DockTab = 'events' | 'results' | 'photos' | 'why' | 'agents';

const AGENT_SCHEMAS = new Set<string>(AGENT_RECORD_SCHEMAS);
const SUBJECT_KEY = /^(?:entity|entities|vehicle|vehicles|actor|actors|drone|drones|uav|uavs|aircraft|subject|subjects|responder|helpers?|self|target|source)$/i;
const SUBJECT_CONTAINER = /(?:vehicle|drone|uav|aircraft|actor|entity|subject|responder|helper|self)/i;
interface ObservedRef { id: string; generation?: string }

/** Preserve kernel generations without turning them into JavaScript numbers. */
function generationText(value: unknown): string | undefined {
  if (typeof value === 'number' && Number.isSafeInteger(value)) return String(value);
  if (typeof value === 'string' && /^-?\d+$/.test(value)) return value;
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const integer = (value as Record<string, unknown>).$integer;
    if (typeof integer === 'string' && /^-?\d+$/.test(integer)) return integer;
  }
  return undefined;
}

/**
 * Entity identities recorded in one decision payload. An identity with no
 * generation is kept separately: it can associate a decision with a drone id,
 * but it cannot distinguish two generations of the same id.
 */
function observedRefs(value: unknown, parent = '', depth = 0, result: ObservedRef[] = []): ObservedRef[] {
  if (value === null || value === undefined || depth > 12) return result;
  if (Array.isArray(value)) {
    for (const item of value) observedRefs(item, parent, depth + 1, result);
    return result;
  }
  if (typeof value === 'string') {
    if (SUBJECT_KEY.test(parent) && value && !/\s/.test(value)) result.push({ id: value });
    else if (parent.endsWith('_json') && /^[\[{]/.test(value.trim())) {
      try { observedRefs(JSON.parse(value), parent, depth + 1, result); } catch { /* a non-JSON string is not an identity */ }
    }
    return result;
  }
  if (typeof value !== 'object') return result;
  const row = value as Record<string, unknown>;
  const contextual = SUBJECT_KEY.test(parent) || SUBJECT_CONTAINER.test(parent);
  if (typeof row.id === 'string' && (contextual || row.generation !== undefined || row.generation_index !== undefined)) {
    result.push({ id: row.id, generation: generationText(row.generation ?? row.generation_index) });
  }
  for (const [key, child] of Object.entries(row)) observedRefs(child, key, depth + 1, result);
  return result;
}

/** Agent records visible at one cut, with projector subjects retained for exact identities. */
function recordsAt(store: TemporalFeedStore, cut: number): { records: AgentRecordInput[]; subjects: Map<string, EntityKey[]> } {
  const records: AgentRecordInput[] = [];
  const subjects = new Map<string, EntityKey[]>();
  for (const commit of store.commits) {
    if (commit.commitIndex > cut) continue;
    commit.messages.forEach((message, sequence) => {
      const messageSubjects = message.subjects;
      if (messageSubjects?.length) subjects.set(message.id, messageSubjects);
      if (!AGENT_SCHEMAS.has(message.schemaId)) return;
      records.push({ id: message.id, schemaId: message.schemaId, atNs: message.at.ns, payload: message.payload, cut: commit.commitIndex, sequence });
    });
  }
  return { records, subjects };
}

/**
 * A selected drone scopes the dock only when the record can actually identify
 * it. Exact subjects/observation fields must match the generation. Engine-name
 * or payload-id matches are accepted only when the viewed prefix has no
 * conflicting generation: zero means the selection is the only generation
 * evidence; a recorded generation must match it. Otherwise the generation stays
 * unknown and the decision is not attributed to the selection.
 */
function decisionMatches(decision: CanonicalDecision, selected: EntityKey, knownGenerations: ReadonlySet<string>, subjects: Map<string, EntityKey[]>): boolean {
  const exact = new Set<string>();
  let loose = false;
  for (const phase of decision.phases) {
    for (const rawId of phase.rawIds) for (const key of subjects.get(rawId) ?? []) if (key.id === selected.id) exact.add(String(key.generation));
    for (const ref of observedRefs(phase.data)) {
      if (ref.id !== selected.id) continue;
      if (ref.generation !== undefined) exact.add(ref.generation);
      else loose = true;
    }
  }
  if (exact.size > 0) return exact.has(String(selected.generation));
  const slash = decision.decisionId.lastIndexOf('/');
  const engine = slash > 0 ? decision.decisionId.slice(0, slash) : decision.decisionId;
  const normalized = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const engineMatch = engine === selected.id
    || ['.', '_', ':', '/'].some(separator => engine.endsWith(separator + selected.id))
    || normalized(engine) === normalized(selected.id)
    || normalized(engine).endsWith(`_${normalized(selected.id)}`);
  if (!engineMatch && !loose) return false;
  return knownGenerations.size === 0 || knownGenerations.size === 1 && knownGenerations.has(String(selected.generation));
}

function modelCallName(data: unknown): string | undefined {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return undefined;
  const row = data as Record<string, unknown>;
  for (const key of ['model', 'provider', 'node']) if (typeof row[key] === 'string') return row[key] as string;
  if (Array.isArray(row.identity)) {
    const text = row.identity.filter(value => typeof value === 'string');
    if (text.length > 0) return text.join(' / ');
  }
  return undefined;
}

function proposalName(kind: unknown, L: (en: string, zh: string) => string): string {
  return kind === 'event' ? L('Proposed event', '建议事件') : kind === 'command' ? L('Proposed command', '建议命令') : L('Proposed fact', '建议事实');
}

function DecisionCard({ decision, locale, startNs, L, href }: {
  decision: CanonicalDecision; locale: string; startNs: string;
  L: (en: string, zh: string) => string; href: string;
}) {
  return <article className="wb-agent-decision" data-testid="wb-agent-decision" data-decision-id={decision.decisionId} style={{ borderTop: '1px solid var(--wb-line)', padding: '8px 0' }}>
    <header style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
      <strong>{L('Decision', '决策')} <span className="wb-mono">{decision.decisionId}</span></strong>
      <span className="wb-tag">{displayTime(decision.simNs, startNs)}</span>
      <span>{L(`${decision.counts.records} record${decision.counts.records === 1 ? '' : 's'}`, `${decision.counts.records} 条记录`)}</span>
    </header>
    <ul style={{ listStyle: 'none', padding: 0, margin: '5px 0' }}>
      {decision.phases.map(phase => <li key={phase.recordId} data-phase={phase.phase} style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', margin: '2px 0' }}>
        <span className="wb-tag">{readableLabel(phase.phase, locale)}</span>
        <span className="wb-mono" data-testid="wb-agent-record-id">{phase.recordId}</span>
        <span>{`${L('cut', '记录位置')} ${phase.cut}`}</span>
        {phase.sourceMessageId && phase.sourceMessageId !== phase.recordId && <span>{`${L('source', '来源')} ${phase.sourceMessageId}`}</span>}
      </li>)}
    </ul>
    {decision.modelCalls.length > 0 && <div data-testid="wb-agent-tool-calls">
      <b>{L('Tool calls', '工具调用')}</b> <span className="wb-tag">{decision.modelCalls.length}</span>
      <ul style={{ margin: '3px 0', paddingLeft: 18 }}>
        {decision.modelCalls.map(phase => <li key={phase.recordId}>
          {modelCallName(phase.data) ?? readableLabel('model_call', locale)} · <span className="wb-mono">{phase.recordId}</span> · {L('cut', '记录位置')} {phase.cut}
        </li>)}
      </ul>
    </div>}
    {decision.calls.length > 0 && <div data-testid="wb-agent-proposed-calls">
      <b>{L('Proposed calls', '建议调用')}</b>
      <ul style={{ margin: '3px 0', paddingLeft: 18 }}>
        {decision.calls.map((call, index) => <li key={`${call.callId}/${index}`}>
          <span className="wb-tag">{call.schema}</span> {call.target} · {locale === 'zh-CN' ? call.summaryZh ?? call.summary : call.summary}
        </li>)}
      </ul>
    </div>}
    {decision.outputs.length > 0 && <div data-testid="wb-agent-proposals">
      <b>{L('Proposals', '建议')}</b>
      <ul style={{ margin: '3px 0', paddingLeft: 18 }}>
        {decision.outputs.map((output, index) => <li key={index} data-proposal={String(output.kind)}>
          {proposalName(output.kind, L)} · <span className="wb-mono">{String(output.schema ?? output.field ?? '')}</span>
        </li>)}
      </ul>
    </div>}
    {decision.receipts.length > 0 && <div data-testid="wb-agent-receipts">
      <b>{L('Actual receipts', '实际回执')}</b>
      <ul style={{ margin: '3px 0', paddingLeft: 18 }}>
        {decision.receipts.map((receipt, index) => <li key={`${receipt.commandId}/${index}`} data-receipt={receipt.status}>
          <span className="wb-tag">{readableLabel(receipt.status, locale)}</span> <span className="wb-mono">{receipt.commandId}</span> · {L('call', '调用')} <span className="wb-mono">{receipt.callId}</span>
        </li>)}
      </ul>
    </div>}
    <a href={href} data-testid="wb-agent-console-link">{L('Open the full agent console', '打开完整智能体控制台')} →</a>
  </article>;
}

/** Playback (browsing the record) — separate from simulation control in the top bar. */
export function Dock({ session, events, kpiIds, kpis, series, entityNames, selected, tab, onTab, open, onToggle, onEvent, why, photos, untilNs, finished = false }: {
  session: RunSession; events: RecordedBusinessEvent[]; kpiIds?: string[]; kpis: KpiValues; series: KpiSample[]; entityNames: Map<string, string>;
  selected?: EntityKey; tab: DockTab; onTab: (tab: DockTab) => void; open: boolean; onToggle: () => void; onEvent: (event: RecordedBusinessEvent) => void; why?: ReactNode; photos?: ReactNode; untilNs?: string; finished?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const { store, clock } = session;
  const end = Math.max(0, store.commits.length - 1);
  const cursor = session.cut === undefined ? store.commits.reduce((last, commit, index) => BigInt(commit.at.ns) <= BigInt(clock.ns) ? index : last, -1) : store.commits.findIndex(commit => commit.commitIndex === session.cut);
  const cut = session.cut ?? (cursor >= 0 ? store.commits[cursor]?.commitIndex : undefined);
  const latest = events.at(-1);
  const startNs = store.header.start.ns;
  const hasAgentRecords = useMemo(() => store.commits.some(commit => commit.messages.some(message => AGENT_SCHEMAS.has(message.schemaId))), [store, store.revision]);
  const agents = useMemo(() => {
    if (cut === undefined) return { decisions: [] as CanonicalDecision[], problems: [] as string[] };
    const { records, subjects } = recordsAt(store, cut);
    const entities = new Set([...store.entities.values()].map(entity => entity.key.id));
    const generations = new Set<string>();
    if (selected) for (const commit of store.commits) {
      if (commit.commitIndex > cut) continue;
      for (const key of commit.created) if (key.id === selected.id) generations.add(String(key.generation));
    }
    const timeline = canonicaliseRecords(records, { entities });
    return {
      decisions: selected ? timeline.decisions.filter(decision => decisionMatches(decision, selected, generations, subjects)) : timeline.decisions,
      problems: timeline.problems,
    };
  }, [store, store.revision, cut, selected?.id, selected?.generation]);
  const api = new URLSearchParams(window.location.search).get('api');
  const agentQuery = new URLSearchParams();
  if (api) agentQuery.set('api', api);
  if (cut !== undefined && cut > 0) agentQuery.set('cut', String(cut));
  const agentSearch = agentQuery.toString();
  const agentHref = `/agents/${encodeURIComponent(store.header.runId)}${agentSearch ? `?${agentSearch}` : ''}`;
  return <section className={`wb-dock wb-glass${open ? ' is-open' : ''}`} aria-label={L('Timeline and events', '时间轴与事件')} data-testid="wb-dock">
    <div className="wb-dock-bar">
      <span className="wb-label">{L('Playback', '回放')}</span>
      <button className="wb-btn icon" aria-label={clock.playing ? L('Pause playback', '暂停回放') : L('Play recorded timeline', '播放已记录的时间轴')} disabled={!store.commits.length} onClick={session.togglePlayback} data-testid="wb-playback">{clock.playing ? '❚❚' : '▶'}</button>
      <select aria-label={L('Playback speed', '回放速度')} value={clock.speed} onChange={event => session.setSpeed(Number(event.target.value))} className="wb-btn small" style={{ width: 64 }}>
        {[0.5, 1, 2, 4, 8, 16, 32].map(speed => <option key={speed} value={speed}>{speed}×</option>)}
      </select>
      <div className="wb-track" data-testid="wb-track">
        <input type="range" min={0} max={end} value={Math.max(0, cursor)} disabled={!end} aria-label={L('Recorded timeline', '已记录的时间轴')} onChange={event => session.seekOffset(Number(event.target.value))} />
        <div className="wb-marks">{events.slice(-300).map(event => <button key={event.key} style={{ left: `${end ? event.offset / end * 100 : 0}%`, background: CATEGORY_STYLE[event.category].color }}
          title={`${displayTime(event.at_ns, startNs)} · ${eventNarrative(event, locale, entityNames)}`} aria-label={eventNarrative(event, locale, entityNames)} onClick={() => onEvent(event)} />)}</div>
      </div>
      {/* A finished run that stopped early recorded less than its planned horizon; show what exists. */}
      <span className="wb-time wb-mono">{displayTime(clock.ns, startNs)} / {untilNs && !(finished && BigInt(clock.end) < BigInt(untilNs)) ? displayTime(untilNs, startNs) : displayTime(clock.end, startNs)}
        {finished && untilNs && BigInt(clock.end) < BigInt(untilNs) ? <small className="wb-time-note">{L(` recorded · ${displayTime(untilNs, startNs)} planned`, ` 已记录 · 计划 ${displayTime(untilNs, startNs)}`)}</small> : null}</span>
    </div>
    <div className="wb-dock-tabs">
      <button aria-selected={tab === 'events' && open} onClick={() => { onTab('events'); if (!open) onToggle(); }}>{L('Events', '事件')} <span className="wb-tag">{events.length}</span></button>
      <button aria-selected={tab === 'results' && open} onClick={() => { onTab('results'); if (!open) onToggle(); }}>{L('Results', '结果')}</button>
      {photos && <button aria-selected={tab === 'photos' && open} onClick={() => { onTab('photos'); if (!open) onToggle(); }}>{L('Photos', '照片')}</button>}
      {why && <button aria-selected={tab === 'why' && open} onClick={() => { onTab('why'); if (!open) onToggle(); }}>{L('Why?', '为什么？')}</button>}
      {hasAgentRecords && <button aria-selected={tab === 'agents' && open} onClick={() => { onTab('agents'); if (!open) onToggle(); }}>{L('Agents', '智能体')} <span className="wb-tag">{agents.decisions.length}</span></button>}
      <div className="wb-ticker" aria-live="polite">{latest ? <><b>{displayTime(latest.at_ns, startNs)}</b> · {eventNarrative(latest, locale, entityNames)}</> : L('No business events recorded yet.', '尚未记录业务事件。')}</div>
      <button onClick={onToggle} aria-expanded={open} aria-label={open ? L('Collapse dock', '收起') : L('Expand dock', '展开')}>{open ? '▾' : '▴'}</button>
    </div>
    {open && <div className="wb-dock-body">
      {tab === 'events' && <EventFeed events={events} entityNames={entityNames} available={store.commits.some(commit => commit.events !== undefined)} selectedEntities={selected ? [selected.id] : []} rawRecordsHref={agentHref} onSelect={event => { const row = events.find(item => item === event); if (row) onEvent(row); }} />}
      {tab === 'results' && <KpiPanel ids={kpiIds} values={kpis} series={series} loading={false} />}
      {tab === 'photos' && photos}
      {tab === 'why' && why}
      {tab === 'agents' && hasAgentRecords && <div data-testid="wb-agent-list">
        {agents.problems.length > 0 && <p className="ct-note">{L(`${agents.problems.length} agent record${agents.problems.length === 1 ? '' : 's'} could not be read.`, `${agents.problems.length} 条智能体记录无法读取。`)}</p>}
        {agents.decisions.length === 0 ? <p className="ct-note">{selected ? L('No agent decisions for the selected drone and generation at this cut.', '此时刻没有所选无人机及代际的智能体决策。') : L('No agent decisions recorded at this cut.', '此时刻没有记录智能体决策。')}</p>
          : agents.decisions.map(decision => <DecisionCard key={decision.decisionId} decision={decision} locale={locale} startNs={startNs} L={L} href={agentHref} />)}
      </div>}
    </div>}
  </section>;
}
