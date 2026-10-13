import { useMemo, useState } from 'react';
import { Leaf, pointer } from '../form';
import { readableLabel } from '../../pages/inspection-format';
import type { SectionProps } from './types';
import { useCatalog, type RelationRow } from './objects/model';

const RESOURCE_FACT = /(capacity|open|power|queue|slots|preparation|stock|pads?_|_count)/;
const packageDocument = (spec: Record<string, any>) => spec.document ?? spec;
// RelationRule.ids is a glob over sources, so escape literal metacharacters in an instance id.
const sourceScope = (id: string) => id.replace(/[\[*?]/g, char => char === '[' ? '[[]' : `[${char}]`);

export function RelationsSection({ studioApi, project, draft, edit, replaceDraft, focus, zh, L, onInspectContract, goTo }: SectionProps) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const engines = (draft.engines ?? {}) as Record<string, any>;
  const [adding, setAdding] = useState(false);
  const { catalog, catalogError } = useCatalog(studioApi, project.id, adding);
  const [relationId, setRelationId] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [targetId, setTargetId] = useState('');
  const [writerId, setWriterId] = useState('');
  const [removing, setRemoving] = useState<string>();
  const rules = useMemo(() => ((draft.bindings as Record<string, any> | undefined)?.relations ?? []) as Array<Record<string, any>>, [draft.bindings]);
  const behaviourEngines = Object.entries(engines).filter(([, engine]) => engine.plugin === 'behaviour');
  const packages = (draft.behaviours ?? behaviourEngines[0]?.[1].config?.packages ?? []) as Array<Record<string, any>>;
  const links: Array<{ edge: Record<string, any>; packageIndex: number; edgeIndex: number }> = packages.flatMap((spec, packageIndex) => (packageDocument(spec).bootstrap_relations?.assertions ?? []).map((edge: Record<string, any>, edgeIndex: number) => ({ edge, packageIndex, edgeIndex })));
  const relations: RelationRow[] = catalog?.relations ?? [];
  const chosen = relations.find(row => row.id === relationId);
  const writers = chosen?.valid_writers ?? chosen?.writer_options?.map(option => option.writer) ?? [];
  const writer = writers.includes(writerId) ? writerId : writers[0];
  const option = chosen?.writer_options?.find(row => row.writer === writer);
  const engine = option && engines[option.engine_id];
  // Only the existing behaviour plugin accepts bootstrap_relations. Other partitions may
  // produce edges during execution, but declaring their ownership cannot create an edge.
  const supportsInitialLink = behaviourEngines.length === 1 && engine?.plugin === 'behaviour' && option?.writer === option?.engine_id;
  const typeOf = (id: string) => entities.find(entity => entity.id === id)?.type;
  const matchesType = (id: string, required: string) => {
    const queue = [typeOf(id)], seen = new Set<string>();
    while (queue.length) {
      const type = queue.shift();
      if (!type || seen.has(type)) continue;
      if (type === required) return true;
      seen.add(type); queue.push(...(catalog?.types.find(row => row.id === type)?.parents ?? []));
    }
    return false;
  };
  const endpointBad = chosen && ((sourceId && !matchesType(sourceId, chosen.source_type)) || (targetId && !matchesType(targetId, chosen.target_type)));
  const duplicate = links.some(({ edge }) => edge.relation === relationId && edge.source?.$ref?.id === sourceId && edge.target?.$ref?.id === targetId);
  const related = links.filter(({ edge }) => edge.relation === relationId);
  const sourceLimit = chosen?.targets_per_source.maximum;
  const targetLimit = chosen?.sources_per_target.maximum;
  const cardinalityBad = !!sourceId && !!targetId && !duplicate && (
    typeof sourceLimit === 'number' && related.filter(({ edge }) => edge.source?.$ref?.id === sourceId).length >= sourceLimit ||
    typeof targetLimit === 'number' && related.filter(({ edge }) => edge.target?.$ref?.id === targetId).length >= targetLimit);
  const canAdd = !!chosen && supportsInitialLink && typeof draft.id === 'string' && !!sourceId && !!targetId && !endpointBad && !duplicate && !cardinalityBad;
  const labelOf = (id: string) => {
    const entity = entities.find(row => row.id === id);
    return (zh ? entity?.label_zh ?? entity?.label : entity?.label) ?? id;
  };

  const add = () => {
    if (!canAdd || !chosen || !writer) return;
    const occupied = new Set([...packages.map(spec => packageDocument(spec).id), ...links.map(({ edge }) => edge.id)]);
    let index = 1;
    while (occupied.has(`workbench-link-${index}`)) index++;
    const id = `workbench-link-${index}`;
    const ref = (entityId: string) => ({ $ref: { run_id: draft.id, epoch: '0', id: entityId, generation: 0, type_id: typeOf(entityId) } });
    const pkg = { format: 'aeroagentsim.behaviour-package/v1', id, revision: 1, predicates: {}, chains: {}, bindings: [],
      budgets: { max_transitions_per_instance_per_ns: 64, max_instances: 1 },
      bootstrap_relations: { owner: writer, assertions: [{ id, relation: chosen.id, source: ref(sourceId), target: ref(targetId) }] } };
    const scope = sourceScope(sourceId);
    const nextRules = rules.some(rule => rule.writer === writer && rule.relation === chosen.id && rule.type === chosen.source_type && rule.ids === scope)
      ? rules : [...rules, { writer, relation: chosen.id, type: chosen.source_type, ids: scope }];
    // One draft replacement keeps the assertion and its permission atomic. Existing packages,
    // including path references or compiled IR, remain intact and the loader resolves them.
    replaceDraft({ ...draft, bindings: { ...(draft.bindings as Record<string, any> ?? {}), relations: nextRules }, behaviours: [...packages, pkg] });
    setAdding(false); setRelationId(''); setSourceId(''); setTargetId(''); setWriterId('');
  };
  const removeLink = (packageIndex: number, edgeIndex: number) => {
    const next = packages.map((spec, index) => {
      if (index !== packageIndex) return spec;
      const document = packageDocument(spec);
      const changed = { ...document, bootstrap_relations: { ...document.bootstrap_relations, assertions: document.bootstrap_relations.assertions.filter((_: unknown, index: number) => index !== edgeIndex) } };
      // Edit the authored package; the loader recompiles it, avoiding stale IR dependencies.
      return changed;
    });
    replaceDraft({ ...draft, behaviours: next }); setRemoving(undefined);
  };
  const resources = entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => Object.keys(entity.facts ?? {}).some(key => RESOURCE_FACT.test(key)));
  return <>
    <p className="hint">{L('Initial links between specific instances, their engine write permissions, and facility resource facts. Initial links take effect when the scene is initialized.', '具体实例之间的初始连接、引擎写入权限和设施资源事实。初始连接在场景初始化时生效。')}</p>
    <div style={{ margin: '6px 0' }}><button className="wb-btn" aria-pressed={adding} onClick={() => setAdding(value => !value)}>{adding ? L('Close add relation', '收起添加关系') : L('Add relation', '添加关系')}</button></div>
    {adding && <div className="wb-form-card">
      {catalogError && <p role="alert" className="wb-issues">{L('The relation catalog is unavailable: ', '关系目录不可用：')}{catalogError}</p>}
      {!catalog && !catalogError && <p className="hint" role="status">{L('Loading the relation catalog…', '正在加载关系目录…')}</p>}
      {catalog && <>
        <label className="wb-form-field"><span className="wb-label">{L('relation type', '关系类型')}</span>
          <select value={relationId} onChange={event => { setRelationId(event.target.value); setWriterId(''); }}>
            <option value="">{L('Choose a relation…', '选择关系…')}</option>
            {relations.map(row => <option key={row.id} value={row.id}>{row.id}</option>)}
          </select></label>
        {!relations.length && <div className="wb-empty">{L('This workspace catalog declares no relations, so none can be added from here.', '当前工作区目录未声明任何关系，因此无法在此添加。')}</div>}
        {chosen && <>
          <p className="hint">{L(`Direction is fixed by the contract: source ${chosen.source_type} → target ${chosen.target_type}.`, `方向由契约固定：源 ${chosen.source_type} → 目标 ${chosen.target_type}。`)}</p>
          <label className="wb-form-field"><span className="wb-label">{L('writer engine', '写入引擎')}</span>
            <select value={writer ?? ''} disabled={!writers.length} onChange={event => setWriterId(event.target.value)}>
              {!writers.length && <option value="">{L('No writer available', '没有可用写入者')}</option>}
              {writers.map(id => { const item = chosen.writer_options?.find(row => row.writer === id); return <option key={id} value={id}>{item ? `${id} · ${item.engine_id} (${item.plugin})` : id}</option>; })}
            </select></label>
          {!supportsInitialLink && <div className="wb-form-prerequisite" role="note">
            {!writers.length ? L(`No configured engine writes ${chosen.id}. Configure a behaviour engine and declare its write permission for this relation in Execution components.`, `没有已配置引擎写入 ${chosen.id}。请在“执行组件”配置行为引擎并声明该关系的写入权限。`)
              : L(`Writer ${writer} (${option?.engine_id ?? writer}) has no supported initial-link setup in this scene. Initial links require a configured behaviour engine with write permission for ${chosen.id}.`, `写入者 ${writer}（${option?.engine_id ?? writer}）在此场景中没有可用的初始连接配置。初始连接需要已配置的行为引擎及 ${chosen.id} 的写入权限。`)}
            {goTo && <button className="wb-btn small" onClick={() => goTo('components')}>{L('Execution components', '执行组件')}</button>}
          </div>}
          {typeof draft.id !== 'string' && <p className="wb-form-prerequisite">{L('The scenario needs an identity before an initial link can be authored.', '场景需要标识才能编写初始连接。')}</p>}
          <div className="wb-form-grid">
            <label className="wb-form-field"><span className="wb-label">{L('source instance', '源实例')}</span>
              <select value={sourceId} onChange={event => setSourceId(event.target.value)}><option value="">{L('Choose…', '选择…')}</option>
                {entities.map(entity => <option key={entity.id} value={entity.id}>{labelOf(entity.id)} ({entity.id})</option>)}</select></label>
            <label className="wb-form-field"><span className="wb-label">{L('target instance', '目标实例')}</span>
              <select value={targetId} onChange={event => setTargetId(event.target.value)}><option value="">{L('Choose…', '选择…')}</option>
                {entities.map(entity => <option key={entity.id} value={entity.id}>{labelOf(entity.id)} ({entity.id})</option>)}</select></label>
          </div>
          {!entities.length && <div className="wb-form-prerequisite">{L('Add source and target objects first.', '请先添加源对象和目标对象。')}{goTo && <button className="wb-btn small" onClick={() => goTo('objects')}>{L('Add objects', '添加对象')}</button>}</div>}
          {endpointBad && <p role="alert" className="wb-issues">{L('The chosen endpoint types do not match the relation contract.', '所选端点类型不符合关系契约。')}</p>}
          {duplicate && <p className="hint">{L('This initial link already exists.', '此初始连接已存在。')}</p>}
          {cardinalityBad && <p role="alert" className="wb-issues">{L('This link exceeds the number of endpoints allowed by the relation contract.', '此连接超出了关系契约允许的端点数量。')}</p>}
          <p className="hint">{L('Save and initialize the scene to assert this source → target link through the selected engine.', '保存并初始化场景后，所选引擎会写入此源 → 目标连接。')}</p>
          <button className="wb-btn primary" disabled={!canAdd} onClick={add}>{L('Add relation', '添加关系')}</button>
        </>}
      </>}
    </div>}
    <h4>{L('Initial links', '初始连接')} <span className="wb-tag">{links.length}</span></h4>
    {links.map(({ edge, packageIndex, edgeIndex }) => {
      const key = `link-${packageIndex}-${edgeIndex}`;
      return <div className="wb-form-card" key={key}>
        <b>{labelOf(edge.source?.$ref?.id)} → {labelOf(edge.target?.$ref?.id)}</b>
        <span className="wb-mono">{edge.relation} · {edge.id}</span>
        <button className="wb-btn small" onClick={() => setRemoving(key)}>{L('Remove', '移除')}</button>
        {removing === key && <div className="wb-form-prerequisite">{L(`Remove initial link ${edge.id}: ${edge.source?.$ref?.id} → ${edge.target?.$ref?.id}. Engine write permissions are retained.`, `移除初始连接 ${edge.id}：${edge.source?.$ref?.id} → ${edge.target?.$ref?.id}。保留引擎写入权限。`)}
          <button className="wb-btn small" onClick={() => removeLink(packageIndex, edgeIndex)}>{L('Confirm removal', '确认移除')}</button>
          <button className="wb-btn small ghost" onClick={() => setRemoving(undefined)}>{L('Cancel', '取消')}</button>
        </div>}
      </div>;
    })}
    {!links.length && <div className="wb-empty">{L('No initial links yet. Add one above: choose a supported writer, then its source and target instance.', '尚无初始连接。在上方添加：选择支持初始连接的写入者，再选择源实例和目标实例。')}</div>}
    <h4>{L('Engine write permissions', '引擎写入权限')} <span className="wb-tag">{rules.length}</span></h4>
    {rules.map((rule, index) => <div className="wb-form-card" key={index} data-path={pointer('bindings', 'relations', index)}>
      <b>{readableLabel(rule.relation, zh ? 'zh-CN' : 'en-US')}</b>
      <span className="wb-mono">{rule.relation}</span>
      <Leaf path={pointer('bindings', 'relations', index, 'writer')} label={L('writer engine', '写入引擎')} value={rule.writer} onChange={() => undefined} readOnly />
      <Leaf path={pointer('bindings', 'relations', index, 'ids')} label={L('source-id scope (glob)', '源 id 范围（通配模式）')} value={rule.ids ?? '*'} onChange={() => undefined} readOnly />
      <span className="hint">{L(`Source type: ${rule.type}`, `源类型：${rule.type}`)}</span>
      <button className="wb-btn small" onClick={() => setRemoving(`rule-${index}`)}>{L('Remove permission', '移除权限')}</button>
      {removing === `rule-${index}` && <div className="wb-form-prerequisite">{L(`Remove permission for ${rule.writer} to write ${rule.relation} from ${rule.type} (${rule.ids ?? '*'}). Any links that require it will fail validation.`, `移除 ${rule.writer} 写入 ${rule.relation} 的权限，源类型 ${rule.type}，范围 ${rule.ids ?? '*'}。依赖此权限的连接将无法通过校验。`)}
        <button className="wb-btn small" onClick={() => { edit(pointer('bindings', 'relations'), rules.filter((_, current) => current !== index)); setRemoving(undefined); }}>{L('Confirm removal', '确认移除')}</button>
        <button className="wb-btn small ghost" onClick={() => setRemoving(undefined)}>{L('Cancel', '取消')}</button>
      </div>}
    </div>)}
    <h4 style={{ marginTop: 14 }}>{L('Resource facts', '资源事实')}</h4>
    {!resources.length && <div className="wb-empty">{L('No resource facts in this scenario.', '该场景中没有资源类事实。')}</div>}
    {resources.map(({ entity, index }) => <details key={index} open={focus?.startsWith(pointer('entities', index))}><summary><b>{labelOf(entity.id)}</b> <span className="wb-tag wb-mono">{entity.type}</span></summary>
      {Object.entries(entity.facts ?? {}).filter(([key]) => RESOURCE_FACT.test(key)).map(([key, value]) => <Leaf key={key} path={pointer('entities', index, 'facts', key)} label={readableLabel(key, zh ? 'zh-CN' : 'en-US')} value={value} onChange={edit} focus={focus} />)}
      {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(entity.type)}>{L('Inspect type contract', '查看类型契约')}</button>}
    </details>)}
  </>;
}
