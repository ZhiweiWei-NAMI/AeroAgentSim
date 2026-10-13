import { Leaf, pointer, TreeForm } from '../form';
import { keysPresent } from './KeyTrees';
import type { SectionProps } from './types';

const RESOURCE_FACT = /(capacity|open|power|queue|slots|preparation|stock|pads?_|_count)/;

export function RelationsSection({ draft, edit, focus, L }: SectionProps) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const resources = entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => Object.keys(entity.facts ?? {}).some(key => RESOURCE_FACT.test(key)));
  return <>
    <p className="hint">{L('Facilities and their capacities, open state and service resources. Relations are facts on the instances below.', '设施及其容量、开放状态和服务资源。关系以下列实例上的事实表示。')}</p>
    {keysPresent(draft, ['relations', 'resources']).map(key => <div key={key}><h4>{key}</h4><TreeForm value={draft[key]} path={pointer(key)} label={key} onChange={edit} focus={focus} /></div>)}
    {!resources.length && <div className="wb-empty">{L('No resource facts in this scenario.', '该场景中没有资源类事实。')}</div>}
    {resources.map(({ entity, index }) => <details key={index} open={focus?.startsWith(pointer('entities', index))}><summary><b>{entity.label ?? entity.id}</b> <span className="wb-tag wb-mono">{entity.type}</span></summary>
      {Object.entries(entity.facts ?? {}).filter(([key]) => RESOURCE_FACT.test(key)).map(([key, value]) => <Leaf key={key} path={pointer('entities', index, 'facts', key)} label={key} value={value} onChange={edit} focus={focus} />)}
    </details>)}
  </>;
}
