import { useEffect, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { RunsApi } from '../feeds/http';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { useI18n } from '../i18n/I18nProvider';
import { InspectionPanel } from '../pages/InspectionPanel';
import { readableLabel } from '../pages/inspection-format';
import { RunOperations } from '../behaviours/RunOperations';
import { ChangeList, MomentsList } from '../trace/EntityStory';
import type { CauseRef } from '../trace/cause-trace';
import { entityId, resolveBinding } from '../viewport/bindings';
import { displayName } from '../viewport/semantic';

type Tab = 'state' | 'story' | 'operate';

/** The selected object: state at the viewed moment, its story, and the operations it admits. */
export function InspectorPanel({ api, runId, store, revision, selected, cut, mode, canOperate, viewingHistory, operateRequest, onSelect, onSeekCut, onTrace, onFocus, onFollow, following, onShowGraph, onClose }: {
  api: RunsApi; runId: string; store: TemporalFeedStore; revision: number; selected?: EntityKey; cut?: number; mode: 'live' | 'replay'; canOperate: boolean; viewingHistory?: boolean; operateRequest?: number;
  onSelect: (key: EntityKey) => void; onSeekCut: (cut: number) => void; onTrace: (ref: CauseRef) => void;
  onFocus: () => void; onFollow: () => void; following: boolean; onShowGraph: () => void; onClose: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [tab, setTab] = useState<Tab>('state');
  const body = useRef<HTMLDivElement>(null), handledRequest = useRef<number>();
  useEffect(() => { if (operateRequest) setTab('operate'); }, [operateRequest]);
  useEffect(() => {
    if (!operateRequest || tab !== 'operate' || handledRequest.current === operateRequest || !body.current) return;
    const reveal = () => {
      const card = body.current?.querySelector<HTMLElement>('.injection-card');
      if (!card) return;
      handledRequest.current = operateRequest; observer.disconnect();
      card.scrollIntoView({ block: 'start' });
      card.querySelector<HTMLElement>('input:not(:disabled),select:not(:disabled),textarea:not(:disabled)')?.focus({ preventScroll: true });
    };
    const observer = new MutationObserver(reveal); observer.observe(body.current, { childList: true, subtree: true }); reveal();
    return () => observer.disconnect();
  }, [operateRequest, tab]);
  void revision;
  const entity = selected ? store.entities.get(entityId(selected)) : undefined;
  const spatial = entity ? !!resolveBinding(store.header, entity.typeId) : false;
  return <aside className="wb-panel wb-right wb-glass" aria-label={L('Inspector', '检查器')} data-testid="wb-inspector">
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
    </div> : <div className="wb-entity-head"><div className="wb-empty"><strong>{selected ? L('Object absent at this moment', '此时刻对象不存在') : tab === 'operate' ? L('Scene events', '场景事件') : L('Nothing selected', '未选择对象')}</strong>{selected ? `${selected.id} · ${L('Choose another timeline moment to inspect it.', '选择其他时间点查看此对象。')}` : tab === 'operate' ? L('Choose an authored event below to continue.', '选择下方已定义的事件以继续。') : L('Click a drone, vehicle or site in the scene, the object list or the graph.', '在场景、对象列表或关系图中点击无人机、车辆或站点。')}</div></div>}
    {(entity || tab === 'operate') && <>
      <div className="wb-tabs" role="tablist">
        {(['state', 'story', 'operate'] as Tab[]).map(item => <button key={item} role="tab" disabled={!entity && item !== 'operate'} aria-selected={tab === item} onClick={() => setTab(item)}>{item === 'state' ? L('State', '状态') : item === 'story' ? L('Story', '经历') : L('Operate', '操作')}</button>)}
      </div>
      <div ref={body} className="wb-panel-body" style={{ paddingTop: 10 }}>
        {tab === 'state' && <InspectionPanel store={store} selected={selected} onSeek={onSeekCut} onOpenTrace={ref => onTrace(ref)} />}
        {tab === 'story' && entity && <>
          <MomentsList store={store} entity={entity.key} onOpen={(ref, at) => { onSeekCut(at); onTrace(ref); }} />
          <ChangeList store={store} entity={entity.key} onOpen={(ref, at) => { onSeekCut(at); onTrace(ref); }} />
        </>}
        {tab === 'operate' && <>
          <p className="hint">{canOperate ? L('Introduce an event below, then follow its recorded response in the scene and timeline.', '在下方引入事件，然后在场景和时间轴中查看记录的响应。')
            : viewingHistory ? L('You are viewing history. Back to live before introducing an event.', '正在查看历史。请返回实时后再引入事件。')
            : mode === 'replay' ? L('Replay reads the recorded journal; operations are not available.', '回放只读取已记录的日志，不能执行操作。') : L('Operations are available while the run is initialized, running or paused.', '运行处于已初始化、运行中或暂停时才可执行操作。')}</p>
          <RunOperations api={api} runId={runId} store={store} selected={selected} commitCut={cut} onSelect={onSelect} onSeek={onSeekCut} mode={mode} interactive={canOperate} />
        </>}
      </div>
    </>}
  </aside>;
}
