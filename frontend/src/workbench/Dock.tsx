import type { ReactNode } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import { useI18n } from '../i18n/I18nProvider';
import { CATEGORY_STYLE } from '../newcomers/categories';
import { EventFeed } from '../newcomers/EventFeed';
import { KpiPanel } from '../newcomers/KpiPanel';
import type { KpiSample, KpiValues } from '../newcomers/api';
import { eventNarrative } from '../newcomers/presentation';
import type { RecordedBusinessEvent } from '../newcomers/projection';
import { displayTime } from '../pages/display-time';
import type { RunSession } from './useRunSession';

export type DockTab = 'events' | 'results' | 'photos' | 'why';

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
  const latest = events.at(-1);
  const startNs = store.header.start.ns;
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
      <div className="wb-ticker" aria-live="polite">{latest ? <><b>{displayTime(latest.at_ns, startNs)}</b> · {eventNarrative(latest, locale, entityNames)}</> : L('No business events recorded yet.', '尚未记录业务事件。')}</div>
      <button onClick={onToggle} aria-expanded={open} aria-label={open ? L('Collapse dock', '收起') : L('Expand dock', '展开')}>{open ? '▾' : '▴'}</button>
    </div>
    {open && <div className="wb-dock-body">
      {tab === 'events' && <EventFeed events={events} entityNames={entityNames} available={store.commits.some(commit => commit.events !== undefined)} selectedEntities={selected ? [selected.id] : []} onSelect={event => { const row = events.find(item => item === event); if (row) onEvent(row); }} />}
      {tab === 'results' && <KpiPanel ids={kpiIds} values={kpis} series={series} loading={false} />}
      {tab === 'photos' && photos}
      {tab === 'why' && why}
    </div>}
  </section>;
}
