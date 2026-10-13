import { algorithmSlotLabel } from '../../newcomers/presentation';
import { Leaf, pointer, TreeForm } from '../form';
import { keysPresent } from './KeyTrees';
import type { SectionProps } from './types';

export function AlgorithmsSection({ draft, edit, focus, L, zh, locale, algorithms }: SectionProps) {
  const slots = (draft.algorithms ?? {}) as Record<string, string>;
  return <>
    <p className="hint">{L('Which method chooses plans. Each slot is used by the components listed with it. Changing a slot changes the next run, never a running world.', '用哪种方法选择方案。每个插槽作用于对应的组件。修改只影响下一次运行，不会改变正在运行的世界。')}</p>
    {Object.entries(slots).map(([slot, chosen]) => {
      const options = (algorithms ?? []).filter(item => item.slot === slot);
      const users = Object.entries((draft.engines ?? {}) as Record<string, any>).filter(([, engine]) => engine?.config?.algorithm_config !== undefined || JSON.stringify(engine?.config ?? {}).includes(slot)).map(([name]) => name);
      const current = options.find(item => item.id === chosen);
      return <div key={slot} style={{ marginBottom: 10 }}>
        <Leaf path={pointer('algorithms', slot)} label={algorithmSlotLabel(slot as never, locale)} value={chosen} options={options.map(item => item.id)} onChange={edit} focus={focus} />
        {current && <p className="hint">{(zh ? current.description_zh : undefined) ?? current.description}</p>}
        {users.length > 0 && <div className="wb-card-meta">{users.map(user => <span key={user} className="wb-tag">{L('used by', '作用于')} {user}</span>)}</div>}
      </div>;
    })}
    {!Object.keys(slots).length && <div className="wb-empty">{L('This scenario declares no algorithm slots.', '该场景未声明算法插槽。')}</div>}
    {keysPresent(draft, ['agents', 'decisions']).map(key => <div key={key}><h4>{key}</h4><TreeForm value={draft[key]} path={pointer(key)} label={key} onChange={edit} focus={focus} /></div>)}
  </>;
}
