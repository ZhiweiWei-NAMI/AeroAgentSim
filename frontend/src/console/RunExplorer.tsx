import { useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { entityId } from '../viewport/bindings';
import { entityLabel, readableLabel } from '../pages/inspection-format';
import { useI18n } from '../i18n/I18nProvider';
import { GraphIcon, InspectIcon, StudioIcon } from './icons';
import { Details } from './Details';

export interface ExplorerSelection { id: string; kind: string }
export function RunExplorer({ store, selected, onSelect, onInspect }: { store: TemporalFeedStore; selected?: EntityKey; onSelect: (key: EntityKey) => void; onInspect: (record?: ExplorerSelection) => void }) {
  const { locale } = useI18n();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const [tab, setTab] = useState('entities'), [search, setSearch] = useState('');
  const categories = [{ id: 'entities', label: copy('Entities', '实体') }, { id: 'relations', label: copy('Relations', '关系') }, { id: 'behaviours', label: copy('Behaviours', '行为') }, { id: 'plugins', label: copy('Plugins', '插件') }];
  const rows: Array<{ id: string; title: string; subtitle: string; key?: EntityKey; record?: Record<string, unknown> }> = tab === 'entities'
    ? [...store.entities.values()].map(entity => ({ id: entityId(entity.key), title: entityLabel(entity, locale), subtitle: entity.typeId, key: entity.key }))
    : tab === 'relations' ? [...store.edges.values()].map(edge => ({ id: edge.edgeId, title: readableLabel(edge.relationId, locale), subtitle: `${edge.source.id} → ${edge.target.id}`, key: edge.source, record: edge }))
      : tab === 'behaviours' ? [...[...store.chainInstances.values()].map(chain => ({ id: chain.instanceId, title: readableLabel(chain.templateId, locale), subtitle: readableLabel(chain.state, locale), key: Object.values(chain.roles)[0], record: chain })), ...[...store.predicateTruth.values()].map(predicate => ({ id: predicate.contextId, title: readableLabel(predicate.predicateId, locale), subtitle: readableLabel(predicate.status, locale), key: Object.values(predicate.roles)[0], record: predicate }))]
        : (store.header.engines ?? []).map(engine => ({ id: engine.id, title: readableLabel(engine.id, locale), subtitle: engine.plugin, record: engine }));
  const filtered = rows.filter(row => `${row.id} ${row.title} ${row.subtitle}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return <aside className="run-explorer" aria-label={copy('Run explorer', '运行资源浏览器')}>
    <div className="run-pane-heading"><span><StudioIcon />{copy('Explorer', '资源浏览')}</span><small>{rows.length}</small></div>
    <div className="run-explorer-search"><InspectIcon /><input type="search" aria-label={copy('Search run objects', '搜索运行对象')} placeholder={copy('Search objects…', '搜索对象…')} value={search} onChange={event => setSearch(event.target.value)} /></div>
    <div className="run-explorer-tabs" role="tablist" aria-label={copy('Object category', '对象类别')}>{categories.map(category => <button key={category.id} role="tab" type="button" aria-selected={category.id === tab} onClick={() => { setTab(category.id); onInspect(undefined); }}>{category.label}</button>)}</div>
    <div className="run-explorer-list" role="tabpanel">{filtered.map(row => <button key={row.id} className={`run-explorer-row${tab === 'entities' && selected && row.id === entityId(selected) ? ' is-selected' : ''}`} onClick={() => { if (row.key) onSelect(row.key); onInspect(row.record ? { id: row.id, kind: tab } : undefined); }}><GraphIcon size={14} /><span><strong>{row.title}</strong><small>{row.subtitle}</small></span></button>)}{!filtered.length && <p className="run-explorer-empty">{search ? copy('No matching objects.', '未找到匹配对象。') : copy('No records at this timeline moment.', '此时间点没有记录。')}</p>}</div>
    <div className="run-explorer-foot">{copy('Recorded state at cursor', '游标处的已记录状态')}<code>{store.viewCursor?.knownAt !== undefined ? `#${store.viewCursor.knownAt}` : '—'}</code></div>
  </aside>;
}


const recordFields: Record<string, [string, string]> = {
  id: ['ID', '标识'], plugin: ['Plugin', '插件'], edgeId: ['Relationship ID', '关系标识'], relationId: ['Relationship type', '关系类型'],
  instanceId: ['Instance ID', '实例标识'], templateId: ['Template', '模板'], bindingId: ['Binding', '绑定'], packageDigest: ['Package digest', '行为包摘要'],
  state: ['State', '状态'], lifecycle: ['Lifecycle', '生命周期'], status: ['Status', '状态'], revision: ['Revision', '修订'], op: ['Operation', '操作'], transitionId: ['Transition', '转换'],
  contextId: ['Context ID', '上下文标识'], predicateId: ['Predicate', '谓词'], profile: ['Profile', '求值配置'], value: ['Value', '值'],
};
/** Resolve the selected identity at every cursor update; never retain an old state snapshot. */
export function RunRecordInspector({ store, selection, onEntity }: { store: TemporalFeedStore; selection: ExplorerSelection; onEntity: () => void }) {
  const { t, locale } = useI18n();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const source = selection.kind === 'relations' ? store.edges.get(selection.id)
    : selection.kind === 'behaviours' ? store.chainInstances.get(selection.id) ?? store.predicateTruth.get(selection.id)
      : store.header.engines?.find(engine => engine.id === selection.id);
  const record = source && Object.fromEntries(Object.entries(source));
  const kind = selection.kind === 'relations' ? copy('Relationship', '关系') : selection.kind === 'behaviours' ? copy('Behaviour', '行为') : copy('Plugin', '插件');
  const title = record ? readableLabel(String(record.templateId ?? record.predicateId ?? record.relationId ?? record.id), locale) : kind;
  return <section className="run-record-inspector"><span className="inspection-eyebrow">{kind}</span><h2>{title}</h2><code>{selection.id}</code>
    {record ? <><dl>{Object.entries(record).filter(([,value]) => typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || value === null).map(([key,value]) => <div key={key}><dt>{recordFields[key] ? copy(...recordFields[key]) : key}</dt><dd>{value === null ? copy('Null (recorded)', '空值（已记录）') : typeof value === 'boolean' ? (value ? t('true') : t('false')) : ['state','status','lifecycle','op'].includes(key) ? readableLabel(String(value),locale) : String(value)}</dd></div>)}</dl><Details title={title} buttonLabel={copy('Full record', '完整记录')}><pre>{JSON.stringify(record,null,2)}</pre></Details></> : <p className="inspection-empty">{copy('This record is not active at the selected timeline moment.', '此记录在当前时间点不生效。')}</p>}
    <button className="console-btn" onClick={onEntity}>{copy('Inspect selected entity', '检查选中实体')}</button>
  </section>;
}
