import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { RunOperations } from '../behaviours/RunOperations';
import { RunsApi } from '../feeds/http';
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

function objectOf(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function entityRef(value: unknown): ObservedRef[] {
  const row = objectOf(value);
  return typeof row?.id === 'string' && row.id
    ? [{ id: row.id, generation: generationText(row.generation ?? row.generation_index) }] : [];
}

/** Only an explicit typed reference grants identity during arbitrary traversal. */
function typedRefs(value: unknown, depth = 0): ObservedRef[] {
  if (depth > 12) return [];
  if (Array.isArray(value)) return value.flatMap(child => typedRefs(child, depth + 1));
  const row = objectOf(value);
  if (!row) return [];
  if (Object.keys(row).length === 1 && '$ref' in row) return entityRef(row.$ref);
  return Object.entries(row).flatMap(([key, child]) => {
    if (key.endsWith('_json') && typeof child === 'string' && /^[\[{]/.test(child.trim())) {
      try { return typedRefs(JSON.parse(child), depth + 1); } catch { return []; }
    }
    return typedRefs(child, depth + 1);
  });
}

/**
 * Bare identities need a declared subject path or an observation-schema path.
 * In particular, proposal target/source strings name engine partitions, never
 * entities, and must not reach generation-evidence resolution.
 */
function observedRefs(value: unknown, phase: string): ObservedRef[] {
  const result = typedRefs(value);
  const row = objectOf(value);
  if (!row) return result;
  // Explicit scalar subject paths on the record body. Schema-specific authored
  // paths arrive as projected subjects, not as guesses from payload field names.
  for (const value of [row.subject, row.subjects].flat()) {
    if (typeof value === 'string' && value && !/\s/.test(value)) result.push({ id: value });
  }
  if (phase !== 'observation') return result;
  const observation = objectOf(objectOf(row.initial)?.observation) ?? row;
  if (Array.isArray(observation.fields)) {
    for (const field of observation.fields) result.push(...entityRef(objectOf(field)?.entity));
  }
  if (Array.isArray(observation.relations)) {
    for (const relation of observation.relations) {
      const edges = objectOf(relation)?.edges;
      if (!Array.isArray(edges)) continue;
      for (const edge of edges) {
        result.push(...entityRef(objectOf(edge)?.source), ...entityRef(objectOf(edge)?.target));
      }
    }
  }
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

type GenerationEvidence = Map<string, Array<{ cut: number; generation: string }>>;

/**
 * Only recorded subjects identify an entity. Engine names are a separate
 * namespace; this feed has no authored engine-to-entity mapping to consult.
 * A generation-less subject can use an unambiguous creation already recorded
 * when the decision began, never the selection or a later creation.
 */
function decisionIdentities(decision: CanonicalDecision, subjects: Map<string, EntityKey[]>, creations: GenerationEvidence): Map<string, Set<string>> {
  const identities = new Map<string, Set<string>>();
  const add = (ref: ObservedRef) => {
    const generations = identities.get(ref.id) ?? new Set<string>();
    if (ref.generation !== undefined) generations.add(ref.generation);
    identities.set(ref.id, generations);
  };
  for (const phase of decision.phases) {
    for (const rawId of phase.rawIds) for (const key of subjects.get(rawId) ?? []) add({ id: key.id, generation: generationText(key.generation) });
    observedRefs(phase.data, phase.phase).forEach(add);
    for (const view of phase.views) observedRefs(view.data, view.phase).forEach(add);
  }
  const firstCut = decision.phases[0]?.cut ?? -1;
  for (const [id, generations] of identities) {
    if (generations.size > 0) continue;
    const known = new Set((creations.get(id) ?? []).filter(row => row.cut <= firstCut).map(row => row.generation));
    if (known.size === 1) known.forEach(value => generations.add(value));
  }
  return identities;
}

/** Reuse the existing typed-message disclosure, without enabling operations. */
function RawRecords({ session, cut }: { session: RunSession; cut?: number }) {
  const { t } = useI18n();
  const root = useRef<HTMLDivElement>(null);
  const base = new URLSearchParams(window.location.search).get('api') ?? window.location.origin;
  const api = useMemo(() => new RunsApi(base), [base]);
  const title = t('Decision / LangGraph and typed event records at this cut');
  useLayoutEffect(() => {
    const disclosure = Array.from(root.current?.querySelectorAll('details') ?? []).find(details => details.firstElementChild?.textContent === title);
    if (disclosure) {
      disclosure.open = true;
      disclosure.querySelector('summary')?.focus();
    }
  }, [session.store, title]);
  return <div ref={root} data-testid="wb-raw-records">
    <RunOperations api={api} runId={session.store.header.runId} store={session.store} commitCut={cut}
      mode="replay" interactive={false} onSelect={() => {}} onSeek={session.seekCut} />
  </div>;
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

function DecisionCard({ decision, generationUnknown, locale, startNs, L, href }: {
  decision: CanonicalDecision; generationUnknown: boolean; locale: string; startNs: string;
  L: (en: string, zh: string) => string; href: string;
}) {
  return <article className="wb-agent-decision" data-testid="wb-agent-decision" data-decision-id={decision.decisionId} style={{ borderTop: '1px solid var(--wb-line)', padding: '8px 0' }}>
    <header style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
      <strong>{L('Decision', '决策')} <span className="wb-mono">{decision.decisionId}</span></strong>
      <span className="wb-tag">{displayTime(decision.simNs, startNs)}</span>
      <span>{L(`${decision.counts.records} record${decision.counts.records === 1 ? '' : 's'}`, `${decision.counts.records} 条记录`)}</span>
      {generationUnknown && <span className="wb-tag" data-testid="wb-agent-generation-unknown">{L('generation unknown', '代次未知')}</span>}
    </header>
    <ul style={{ listStyle: 'none', padding: 0, margin: '5px 0' }}>
      {decision.phases.map(phase => <li key={phase.recordId} data-phase={phase.phase} style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', margin: '2px 0' }}>
        <span className="wb-tag">{readableLabel(phase.phase, locale)}</span>
        <span className="wb-mono" data-testid="wb-agent-record-id">{phase.recordId}</span>
        <span>{`${L('cut', '记录位置')} ${phase.cut}`}</span>
        {phase.sourceMessageId && phase.sourceMessageId !== phase.recordId && <span>{`${L('source', '来源')} ${phase.sourceMessageId}`}</span>}
      </li>)}
    </ul>
    {decision.modelCalls.length > 0 && <div data-testid="wb-agent-model-calls">
      <b>{L('Model calls', '模型调用')}</b> <span className="wb-tag">{decision.modelCalls.length}</span>
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
  const [showRawRecords, setShowRawRecords] = useState(false);
  const agents = useMemo(() => {
    const { records, subjects } = recordsAt(store, cut ?? -1);
    const entities = new Set([...store.entities.values()].map(entity => entity.key.id));
    const creations: GenerationEvidence = new Map();
    for (const commit of store.commits) {
      if (commit.commitIndex > (cut ?? -1)) continue;
      for (const key of commit.created) {
        const rows = creations.get(key.id) ?? [];
        rows.push({ cut: commit.commitIndex, generation: String(key.generation) });
        creations.set(key.id, rows);
      }
    }
    const timeline = canonicaliseRecords(records, { entities });
    const decisions = timeline.decisions.map(decision => {
      const identities = decisionIdentities(decision, subjects, creations);
      return { decision, identities, generationUnknown: identities.size === 0 || [...identities.values()].some(generations => generations.size === 0) };
    });
    return {
      decisions: selected ? decisions.filter(row => row.identities.get(selected.id)?.has(String(selected.generation))) : decisions,
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
      {tab === 'events' && <>
        <EventFeed events={events} entityNames={entityNames} available={store.commits.some(commit => commit.events !== undefined)} selectedEntities={selected ? [selected.id] : []} onExplore={() => setShowRawRecords(true)} onSelect={event => { const row = events.find(item => item === event); if (row) onEvent(row); }} />
        {showRawRecords && <RawRecords key={store.header.runId} session={session} cut={cut} />}
      </>}
      {tab === 'results' && <KpiPanel ids={kpiIds} values={kpis} series={series} loading={false} />}
      {tab === 'photos' && photos}
      {tab === 'why' && why}
      {tab === 'agents' && hasAgentRecords && <div data-testid="wb-agent-list">
        {agents.problems.length > 0 && <p className="ct-note">{L(`${agents.problems.length} agent record${agents.problems.length === 1 ? '' : 's'} could not be read.`, `${agents.problems.length} 条智能体记录无法读取。`)}</p>}
        {agents.decisions.length === 0 ? <p className="ct-note">{selected ? L('No agent decisions for the selected drone and generation at this cut.', '此时刻没有所选无人机及代际的智能体决策。') : L('No agent decisions recorded at this cut.', '此时刻没有记录智能体决策。')}</p>
          : agents.decisions.map(({ decision, generationUnknown }) => <DecisionCard key={decision.decisionId} decision={decision} generationUnknown={generationUnknown} locale={locale} startNs={startNs} L={L} href={agentHref} />)}
      </div>}
    </div>}
  </section>;
}
