import { algorithmSlotLabel } from '../../newcomers/presentation';
import { Leaf, pointer, TreeForm } from '../form';
import { keysPresent } from './KeyTrees';
import type { SectionProps } from './types';

/** C8 contract: algorithm descriptors may carry `consumers: [{engine, plugin}]` naming the components
 *  that actually read that slot at runtime. Absent consumers means unknown, never "everything". */
export function AlgorithmsSection({ draft, edit, focus, zh, locale, algorithms, L }: SectionProps) {
  const slots = (draft.algorithms ?? {}) as Record<string, string>;
  return <>
    <p className="hint">{L('Which method chooses plans. Changes affect the next run only — a running world keeps its pinned algorithms.', '用哪种方法选择方案。修改只影响下一次运行——正在运行的世界保持已固定的算法。')}</p>
    {Object.entries(slots).map(([slot, chosen]) => {
      const current = (algorithms ?? []).find(item => item.id === chosen && item.slot === slot);
      const consumers = current?.consumers;
      return <div key={slot} style={{ marginBottom: 10 }}>
        <Leaf path={pointer('algorithms', slot)} label={algorithmSlotLabel(slot as never, locale)} value={chosen} options={(algorithms ?? []).filter(item => item.slot === slot).map(item => item.id)} onChange={edit} focus={focus} />
        {current && <p className="hint">{(zh ? current.description_zh : undefined) ?? current.description}</p>}
        {consumers
          ? consumers.length > 0 && <div className="wb-card-meta">{consumers.map(consumer => <span key={`${consumer.engine}/${consumer.plugin}`} className="wb-tag">{L('used by', '作用于')} <span className="wb-mono">{consumer.engine}</span> ({consumer.plugin})</span>)}</div>
          : <p className="hint">{L('Scope unknown for this algorithm; the catalog does not say which components consume it.', '该算法的作用范围未知；目录未说明哪些组件会使用它。')}</p>}
      </div>;
    })}
    {!Object.keys(slots).length && <div className="wb-empty">{L('This scenario declares no algorithm slots.', '该场景未声明算法插槽。')}</div>}
    {keysPresent(draft, ['agents', 'decisions']).map(key => <div key={key}><h4>{key}</h4><TreeForm value={draft[key]} path={pointer(key)} label={key} onChange={edit} focus={focus} /></div>)}
  </>;
}
