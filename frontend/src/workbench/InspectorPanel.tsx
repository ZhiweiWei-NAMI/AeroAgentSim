import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { RunsApi } from '../feeds/http';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { useI18n } from '../i18n/I18nProvider';
import { InspectionPanel } from '../pages/InspectionPanel';
import { readableLabel, displayLabel, displayUnit, stateValue } from '../pages/inspection-format';
import { exactValue } from '../feeds/format';
import type { OperatorInput } from './useOperatorInput';
import { RunOperations } from '../behaviours/RunOperations';
import { ChangeList, MomentsList } from '../trace/EntityStory';
import type { CauseRef } from '../trace/cause-trace';
import { entityId, resolveBinding } from '../viewport/bindings';
import { displayName } from '../viewport/semantic';

type Tab = 'state' | 'story' | 'operate';

/** T3-006: lead with the fields an operator actually needs, in this order. */
const OPERATING_FIELDS = [
  /status|state$|lifecycle/i,
  /battery|charg(?:e|ing)|energy|soc/i,
  /(?:^|[.:])position(?:_enu_m)?$|location|coord/i,
  /task|mission|order|assignment/i,
  /speed|velocity/i,
  /altitude|height/i,
];

function operatingFields(fields: Array<[string, unknown]>): Array<[string, unknown]> {
  const score = (id: string) => OPERATING_FIELDS.findIndex(row => row.test(id));
  const sorted = [...fields].sort((a, b) => {
    const sa = score(a[0]), sb = score(b[0]);
    return (sa === -1 ? OPERATING_FIELDS.length : sa) - (sb === -1 ? OPERATING_FIELDS.length : sb);
  });
  const isEmpty = (fact: unknown) => {
    const value = fact && typeof fact === 'object' && 'value' in (fact as Record<string, unknown>) ? (fact as Record<string, unknown>).value : fact;
    return value === undefined || value === null || value === '' || Array.isArray(value) && value.length === 0;
  };
  return sorted.filter(([id, value]) => score(id) !== -1 && !isEmpty(value));
}

/** The selected object: state at the viewed moment, its story, and the operations it admits. */
export function InspectorPanel({ api, runId, store, revision, selected, cut, mode, canOperate, viewingHistory, operateRequest, onSelect, onSeekCut, onTrace, onFocus, onFollow, following, onShowGraph, onClose, inputSource, onCompactChange }: {
  api: RunsApi; runId: string; store: TemporalFeedStore; revision: number; selected?: EntityKey; cut?: number; mode: 'live' | 'replay'; canOperate: boolean; viewingHistory?: boolean; operateRequest?: number;
  inputSource?: OperatorInput; onCompactChange?: (compact: boolean) => void;
  onSelect: (key: EntityKey) => void; onSeekCut: (cut: number) => void; onTrace: (ref: CauseRef) => void;
  onFocus: () => void; onFollow: () => void; following: boolean; onShowGraph: () => void; onClose: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [tab, setTab] = useState<Tab>('state');
  const body = useRef<HTMLDivElement>(null), handledRequest = useRef<number>();
  const scrollPositions = useRef<Record<Tab, number>>({ state: 0, story: 0, operate: 0 });
  const cancelScrollRestore = useRef<() => void>();
  useEffect(() => { if (operateRequest) setTab('operate'); }, [operateRequest]);
  useEffect(() => {
    if (!operateRequest || tab !== 'operate' || handledRequest.current === operateRequest || !body.current) return;
    const reveal = () => {
      const card = body.current?.querySelector<HTMLElement>('.injection-card');
      if (!card) return;
      cancelScrollRestore.current?.();
      handledRequest.current = operateRequest; observer.disconnect();
      card.scrollIntoView({ block: 'start' });
      card.querySelector<HTMLElement>('input:not(:disabled),select:not(:disabled),textarea:not(:disabled)')?.focus({ preventScroll: true });
    };
    const observer = new MutationObserver(reveal); observer.observe(body.current, { childList: true, subtree: true }); reveal();
    return () => observer.disconnect();
  }, [operateRequest, tab]);
  void revision;
  const entity = selected ? store.entities.get(entityId(selected)) : undefined;
  const compact = !entity && tab !== 'operate';
  // Restore before painting the new content; scrolling an operation form must
  // not hide the first State fields. An explicit event request still reveals
  // its form with the effect above.
  useLayoutEffect(() => {
    const element = body.current, wanted = scrollPositions.current[tab];
    if (!element) return;
    element.scrollTop = wanted;
    if (element.scrollTop === wanted) return;
    // Operate loads its form asynchronously. Retain the saved position until
    // the content can accommodate it; stop observing as soon as it is restored
    // or the user takes over the scroll.
    let pending = true;
    const cancel = () => { pending = false; observer.disconnect(); cancelScrollRestore.current = undefined; };
    const restore = () => { if (!pending) return; element.scrollTop = wanted; if (element.scrollTop === wanted) cancel(); };
    const observer = new ResizeObserver(restore);
    cancelScrollRestore.current = cancel;
    for (const child of element.children) observer.observe(child);
    return cancel;
  }, [tab, compact]);
  // Before paint, so floating controls never sit under a widened inspector.
  useLayoutEffect(() => { onCompactChange?.(compact); }, [compact, onCompactChange]);
  const spatial = entity ? !!resolveBinding(store.header, entity.typeId) : false;
  return <aside className={`wb-panel wb-right wb-glass${compact ? ' is-compact' : ''}`} aria-label={L('Inspector', '检查器')} data-testid="wb-inspector" data-compact={compact}>
    <div className="wb-panel-head"><h3>{L('Inspector', '检查器')}</h3><button className="wb-x" aria-label={L('Hide inspector', '隐藏检查器')} onClick={onClose}>›</button></div>
    {entity ? <div className="wb-entity-head">
      <span className="wb-tag wb-mono">{readableLabel(entity.typeId, locale)}</span>
      <h2>{displayName(entity, store.header, locale)}</h2>
      <p className="wb-mono" style={{ fontSize: 11 }}>{entity.key.id}{cut !== undefined ? ` · ${L('as of journal cut', '日志位置')} #${cut}` : ''}</p>
      <div className="wb-entity-actions">
        {spatial && <button className="wb-btn small" onClick={onFocus}>{L('Focus', '聚焦')}</button>}
        {spatial && <button className="wb-btn small" aria-pressed={following} onClick={onFollow}>{L('Follow', '跟随')}</button>}
        <button className="wb-btn small" onClick={onShowGraph}>{L('Show in graph', '在关系图中查看')}</button>
        {!spatial && <span className="wb-tag" title={L('This object has no position; it is shown in the graph and lists, never placed in 3D.', '此对象没有空间位置，只在关系图和列表中显示，不会放进三维场景。')}>{L('non-spatial', '非空间')}</span>}
      </div>
    </div> : <div className="wb-entity-head"><div className="wb-empty"><strong>{selected ? L('Object absent at this moment', '此时刻对象不存在') : tab === 'operate' ? L('Scene events', '场景事件') : L('Nothing selected', '未选择对象')}</strong>{selected ? `${selected.id} · ${L('Choose another timeline moment to inspect it.', '选择其他时间点查看此对象。')}` : tab === 'operate' ? L('Choose an authored event below to continue.', '选择下方已定义的事件以继续。') : L('Click a drone, vehicle or site in the scene, the object list or the graph.', '在场景、对象列表或关系图中点击无人机、车辆或站点。')}</div>{compact && canOperate && <button className="wb-btn small" onClick={() => setTab('operate')}>{L('Introduce an event', '引入事件')}</button>}</div>}
    {(entity || tab === 'operate') && <>
      <div className="wb-tabs" role="tablist">
        {(['state', 'story', 'operate'] as Tab[]).map(item => <button key={item} role="tab" disabled={!entity && item !== 'operate'} aria-selected={tab === item} onClick={() => setTab(item)}>{item === 'state' ? L('State', '状态') : item === 'story' ? L('Story', '经历') : L('Operate', '操作')}</button>)}
      </div>
      <div ref={body} className="wb-panel-body" style={{ paddingTop: 10 }}
        onWheel={() => cancelScrollRestore.current?.()} onPointerDown={() => cancelScrollRestore.current?.()} onKeyDown={() => cancelScrollRestore.current?.()}
        onScroll={event => { if (!cancelScrollRestore.current) scrollPositions.current[tab] = event.currentTarget.scrollTop; }}>
        {tab === 'state' && <>
          <OperatingSummary store={store} selected={selected} />
          <details className="wb-operating-more" data-testid="wb-all-fields"><summary>{L('All fields','全部字段')}</summary>
            <InspectionPanel store={store} selected={selected} onSeek={onSeekCut} onOpenTrace={ref => onTrace(ref)} />
          </details>
        </>}
        {tab === 'story' && entity && <>
          <MomentsList store={store} entity={entity.key} onOpen={(ref, at) => { onSeekCut(at); onTrace(ref); }} />
          <ChangeList store={store} entity={entity.key} onOpen={(ref, at) => { onSeekCut(at); onTrace(ref); }} />
        </>}
        {tab === 'operate' && <>
          <p className="hint">{canOperate ? L('Introduce an event below, then follow its recorded response in the scene and timeline.', '在下方引入事件，然后在场景和时间轴中查看记录的响应。')
            : viewingHistory ? L('You are viewing history. Back to live before introducing an event.', '正在查看历史。请返回实时后再引入事件。')
            : mode === 'replay' ? L('Replay reads the recorded journal; operations are not available.', '回放只读取已记录的日志，不能执行操作。') : L('Operations are available while the run is running or paused.', '运行处于运行中或暂停时才可执行操作。')}</p>
          <RunOperations api={api} runId={runId} store={store} selected={selected} commitCut={cut} onSelect={onSelect} onSeek={onSeekCut} mode={mode} interactive={canOperate} inputSource={inputSource} />
        </>}
      </div>
    </>}
  </aside>;
}

/** T3-006: the operating state a dispatcher asks for first, above the full fact list. */
function OperatingSummary({ store, selected }: { store: TemporalFeedStore; selected?: EntityKey }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, zhText: string) => zh ? zhText : en;
  const entity = selected ? store.entities.get(entityId(selected)) : undefined;
  if (!entity) return null;
  const primary = operatingFields([...entity.fields]);
  const factValue = (fact: unknown): unknown => fact && typeof fact === 'object' && 'value' in (fact as Record<string, unknown>) ? (fact as Record<string, unknown>).value : fact;
  const shortValue = (value: unknown): string => {
    const raw = factValue(value);
    return stateValue(raw,locale) ?? exactValue(raw);
  };
  const rows = primary.map(([id,value]) => {
    const field=store.header.fields.find(row=>row.fieldId===id);
    const unit=displayUnit(field,locale) ?? (/(?:^|[.:])soc$/i.test(id) ? L('ratio','比例') : undefined);
    return {id,title:displayLabel(field?.displayName,id,locale),unit,value:shortValue(value)};
  });
  if (!rows.length) return null;
  return <section className="wb-operating" data-testid="wb-operating" aria-label={L('Operating state', '运行状态')}>
    {rows.map(row => <div key={row.id} className="wb-operating-row">
      <span className="wb-operating-label">{row.title}{row.unit && <small> ({row.unit})</small>}</span>
      <span className="wb-mono">{row.value}</span>
    </div>)}
  </section>;
}
