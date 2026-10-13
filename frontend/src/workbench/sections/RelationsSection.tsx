import { useMemo, useState } from 'react';
import { Leaf, pointer } from '../form';
import { readableLabel } from '../../pages/inspection-format';
import type { SectionProps } from './types';
import { useCatalog, type RelationRow } from './objects/model';

const RESOURCE_FACT = /(capacity|open|power|queue|slots|preparation|stock|pads?_|_count)/;
/** The scenario loader accepts only this relation edge shape under bindings.relations[]. */
const relationEdge = (writer: string, relation: string, type: string) => ({ writer, relation, type });

/** One authored relation edge under /bindings/relations. */
function RelationCard({ edge, index, onRemove, zh, L }: {
  edge: Record<string, any>; index: number;
  onRemove: () => void; zh: boolean; L: (en: string, zh: string) => string;
}) {
  const locale = zh ? 'zh-CN' : 'en-US';
  const labelOf = (id: unknown) => typeof id === 'string' ? readableLabel(id, locale) : String(id ?? '?');
  return <div style={{ border: '1px solid var(--wb-line)', borderRadius: 10, padding: '8px 10px', marginBottom: 8 }} data-path={pointer('bindings', 'relations', index)}>
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
      <b>{labelOf(edge.relation)}</b>
      <span className="wb-tag wb-mono">{edge.relation}</span>
      <span className="wb-tag">{labelOf(edge.type)}</span>
      <button className="wb-btn small danger" onClick={onRemove}>{L('Remove', '移除')}</button>
    </div>
    <div style={{ fontSize: 12 }}>
      {labelOf(edge.type)} <span className="wb-mono" style={{ fontSize: 10, color: 'var(--wb-muted)' }}>{String(edge.type ?? '')}</span>
      {' → '}{labelOf(edge.ids)}
      <div className="wb-mono" style={{ fontSize: 10, color: 'var(--wb-muted)' }}>{String(edge.ids ?? '*')}</div>
    </div>
    <Leaf path={pointer('bindings', 'relations', index, 'writer')} label={L('writer engine', '写入引擎')} value={edge.writer} onChange={() => undefined} readOnly />
    <Leaf path={pointer('bindings', 'relations', index, 'ids')} label={L('member id pattern', '成员 id 模式')} value={edge.ids ?? '*'} onChange={() => undefined} readOnly />
  </div>;
}

export function RelationsSection({ studioApi, project, draft, edit, focus, zh, L, onInspectContract }: SectionProps) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const [adding, setAdding] = useState(false);
  const { catalog, catalogError } = useCatalog(studioApi, project.id, adding);
  const [relationId, setRelationId] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [targetId, setTargetId] = useState('');

  const edges = useMemo(() => ((draft.bindings as Record<string, any> | undefined)?.relations ?? []) as Array<Record<string, any>>, [draft.bindings]);
  const relations: RelationRow[] = catalog?.relations ?? [];
  const chosen = relations.find(row => row.id === relationId);
  const entityIds = entities.map(entity => String(entity.id));
  const typeOf = (id: string) => entities.find(entity => entity.id === id)?.type;
  const endpointBad = chosen && (
    (typeOf(sourceId) !== undefined && typeOf(sourceId) !== chosen.source_type) ||
    (typeOf(targetId) !== undefined && typeOf(targetId) !== chosen.target_type));
  const canAdd = !!chosen && entityIds.includes(sourceId) && (entityIds.includes(targetId) || targetId.includes('*')) && !endpointBad;

  const add = () => {
    if (!canAdd || !chosen) return;
    // The loader's contract for bindings.relations[] is {writer, relation, type} plus optional ids.
    const next = [...edges, { ...relationEdge('operations', chosen.id, chosen.source_type), ids: `${sourceId},${targetId}` }];
    edit(pointer('bindings', 'relations'), next);
    setAdding(false); setRelationId(''); setSourceId(''); setTargetId('');
  };
  const remove = (index: number) => {
    const bindings = { ...((draft.bindings ?? {}) as Record<string, any>) };
    bindings.relations = edges.filter((_, current) => current !== index);
    edit(pointer('bindings'), bindings);
  };

  const resources = entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => Object.keys(entity.facts ?? {}).some(key => RESOURCE_FACT.test(key)));
  return <>
    <p className="hint">{L('Typed, directed links between instances, and the resource facts facilities carry. Relations live under bindings.relations in the same shape the runtime reads.', '实例之间有类型、有方向的连接，以及设施携带的资源事实。关系保存在 bindings.relations 下，结构与运行时读取的一致。')}</p>
    <div style={{ margin: '6px 0' }}><button className="wb-btn" aria-pressed={adding} onClick={() => setAdding(value => !value)}>{adding ? L('Close add relation', '收起添加关系') : L('Add relation', '添加关系')}</button></div>
    {adding && <>
      {catalogError && <p role="alert" style={{ color: 'var(--wb-bad)' }}>{L('The relation catalog is unavailable: ', '关系目录不可用：')}{catalogError}</p>}
      {!catalog && !catalogError && <p className="hint" role="status">{L('Loading the relation catalog…', '正在加载关系目录…')}</p>}
      {catalog && <>
        <label className="wb-field"><span>{L('relation type', '关系类型')}</span>
          <select value={relationId} onChange={event => setRelationId(event.target.value)}>
            <option value="">{L('Choose a relation…', '选择关系…')}</option>
            {relations.map(row => <option key={row.id} value={row.id}>{row.id}</option>)}
          </select></label>
        {!relations.length && <div className="wb-empty">{L('This workspace catalog declares no relations, so none can be added from here.', '当前工作区目录未声明任何关系，因此无法在此添加。')}</div>}
        {chosen && <>
          <p className="hint">{L(`Direction is fixed by the contract: source ${chosen.source_type} → target ${chosen.target_type}.`, `方向由契约固定：源 ${chosen.source_type} → 目标 ${chosen.target_type}。`)}</p>
          <div style={{ display: 'grid', gap: 6, gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
            <label className="wb-field"><span>{L('source instance', '源实例')}</span>
              <select value={sourceId} onChange={event => setSourceId(event.target.value)}><option value="">{L('Choose…', '选择…')}</option>
                {entities.map(entity => <option key={entity.id} value={entity.id}>{entity.id}{entity.type !== chosen.source_type ? ` (${L(`type ${entity.type}`, `类型 ${entity.type}`)})` : ''}</option>)}</select></label>
            <label className="wb-field"><span>{L('target instance', '目标实例')}</span>
              <select value={targetId} onChange={event => setTargetId(event.target.value)}><option value="">{L('Choose…', '选择…')}</option>
                {entities.map(entity => <option key={entity.id} value={entity.id}>{entity.id}{entity.type !== chosen.target_type ? ` (${L(`type ${entity.type}`, `类型 ${entity.type}`)})` : ''}</option>)}</select></label>
          </div>
          {endpointBad && <p role="alert" style={{ color: 'var(--wb-bad)' }}>{L('The chosen endpoint types do not match the relation contract; the server would reject this edge.', '所选端点类型不符合关系契约，服务器会拒绝这条边。')}</p>}
          <div style={{ display: 'flex', gap: 8, margin: '6px 0' }}>
            <button className="wb-btn primary" disabled={!canAdd} onClick={add}>{L('Add relation', '添加关系')}</button>
          </div>
        </>}
      </>}
    </>}
    <h4>{L('Relations', '关系')} <span className="wb-tag">{edges.length}</span></h4>
    {edges.map((edge, index) => <RelationCard key={index} edge={edge} index={index} onRemove={() => remove(index)} zh={zh} L={L} />)}
    {!edges.length && <div className="wb-empty">{L('No relations yet. Add one above: choose the relation type from the catalog, then its source and target instance.', '还没有关系。在上方添加：先从目录选择关系类型，再选择源和目标实例。')}</div>}
    <h4 style={{ marginTop: 14 }}>{L('Resource facts', '资源事实')}</h4>
    {!resources.length && <div className="wb-empty">{L('No resource facts in this scenario.', '该场景中没有资源类事实。')}</div>}
    {resources.map(({ entity, index }) => <details key={index} open={focus?.startsWith(pointer('entities', index))}><summary><b>{entity.label ?? entity.id}</b> <span className="wb-tag wb-mono">{entity.type}</span></summary>
      {Object.entries(entity.facts ?? {}).filter(([key]) => RESOURCE_FACT.test(key)).map(([key, value]) => <Leaf key={key} path={pointer('entities', index, 'facts', key)} label={`${readableLabel(key, zh ? 'zh-CN' : 'en-US')}`} value={value} onChange={edit} focus={focus} />)}
      {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(entity.type)}>{L('Inspect type contract', '查看类型契约')}</button>}
    </details>)}
  </>;
}
