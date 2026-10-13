import { useState } from 'react';
import { pointer } from '../../form';
import type { Scenario } from '../types';

/** A fleet is a generation rule in draft.generators, never an already-existing instance. */
export function Fleets({ draft, edit, L }: {
  draft: Scenario; edit: (path: string, value: unknown) => void; L: (en: string, zh: string) => string;
}) {
  const entries = Object.entries((draft.generators ?? {}) as Record<string, Record<string, any>>);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [type, setType] = useState('');
  const [count, setCount] = useState(2);
  const [spawn, setSpawn] = useState('0');
  const [spacing, setSpacing] = useState('35');
  const nameBad = !/^[A-Za-z0-9_-]+$/.test(name) || entries.some(([key]) => key === name);
  const countBad = !Number.isInteger(count) || count < 1;
  const spawnBad = !Number.isFinite(Number(spawn));
  const add = () => {
    if (nameBad || countBad || spawnBad) return;
    edit(pointer('generators', name), {
      count, prefix: `${name}-`, type,
      starts: [[Number(spawn), 0, 0]],
      spacing_m: Number(spacing) || 0,
      facts: {
        'aas.studio.velocity_enu_m_s': [0, 0, 0],
        'aas.studio.energy_j': 100000,
      },
    });
    setAdding(false); setName(''); setType(''); setCount(2); setSpawn('0');
  };
  const remove = (key: string) => {
    const rest = { ...((draft.generators ?? {}) as Record<string, unknown>) };
    delete rest[key];
    edit('generators', rest);
  };
  return <>
    <h4>{L('Generated fleets', '批量生成的机队')}</h4>
    <p className="hint">{L('A fleet is a generation rule, not existing instances: it expands to members at initialization.', '机队是一条生成规则，不是已存在的实例：初始化时才展开成成员。')}</p>
    {entries.map(([key, generator]) => <div key={key} style={{ border: '1px solid var(--wb-line)', borderRadius: 10, padding: '8px 10px', marginBottom: 8 }} data-path={pointer('generators', key)}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <b className="wb-mono">{key}</b>
        <span className="wb-tag wb-mono">{generator.type ?? '?'}</span>
        <span className="wb-tag ok">{L(`will generate ${generator.count ?? '?'}`, `将生成 ${generator.count ?? '?'}`)}</span>
        <span className="wb-tag warn" style={{ flex: 1 }}>{L(`expected at t=0: +${generator.count ?? '?'} (not counted as explicit instances)`, `t=0 预期：+${generator.count ?? '?'}（不计入显式实例）`)}</span>
        <button className="wb-btn small danger" onClick={() => remove(key)}>{L('Remove fleet', '移除机队')}</button>
      </div>
      <label className="wb-field"><span>{`${key} · ${L('count', '数量')}`}</span>
        <input type="number" min={1} value={generator.count ?? ''} onChange={event => edit(pointer('generators', key, 'count'), Number(event.target.value))} /></label>
      <label className="wb-field"><span>{`${key} · ${L('type', '类型')} (${L('read-only; author it in the registry', '只读；请在 registry 中声明')})`}</span>
        <input className="wb-mono" value={generator.type ?? ''} readOnly /></label>
      <label className="wb-field"><span>{`${key} · ${L('first spawn point east (m)', '首个出生点东向 (m)')}`}</span>
        <input inputMode="decimal" value={Array.isArray(generator.starts?.[0]) ? String(generator.starts[0][0]) : ''} onChange={event => {
          const starts = (generator.starts ?? [[0, 0, 0]]).map((row: unknown) => Array.isArray(row) ? [...row] : [0, 0, 0]);
          starts[0] = [Number(event.target.value) || 0, starts[0]?.[1] ?? 0, starts[0]?.[2] ?? 0];
          edit(pointer('generators', key, 'starts'), starts);
        }} /></label>
    </div>)}
    {!entries.length && <div className="wb-empty">{L('No generated fleets.', '没有批量生成的机队。')}</div>}
    {adding ? <div style={{ border: '1px dashed var(--wb-line)', borderRadius: 10, padding: 10, margin: '6px 0' }}>
      <div style={{ display: 'grid', gap: 6, gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
        <label className="wb-field"><span>{L('fleet name', '机队名称')}</span><input value={name} onChange={event => setName(event.target.value)} placeholder="fleet" aria-invalid={nameBad} /></label>
        <label className="wb-field"><span>{L('type id (must exist in the registry)', '类型 id（必须在 registry 中已声明）')}</span><input className="wb-mono" value={type} onChange={event => setType(event.target.value)} placeholder="oo:UAV" /></label>
        <label className="wb-field"><span>{L('count', '数量')}</span><input type="number" min={1} value={count} onChange={event => setCount(Number(event.target.value) || 0)} aria-invalid={countBad} /></label>
        <label className="wb-field"><span>{L('first spawn east (m)', '首个出生点东向 (m)')}</span><input inputMode="decimal" value={spawn} onChange={event => setSpawn(event.target.value)} aria-invalid={spawnBad} /></label>
      </div>
      <p className="wb-add-note">{L('Mirrors the draft generator shape: count, prefix, type, starts, spacing_m and initial facts. Member ids are fleet-1 … fleet-N.', '与草稿生成器结构一致：count、prefix、type、starts、spacing_m 与初始事实。成员 id 为 机队名-1 … 机队名-N。')}</p>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="wb-btn small primary" disabled={nameBad || countBad || spawnBad || !type.trim()} onClick={add}>{L('Add fleet', '添加机队')}</button>
        <button className="wb-btn small ghost" onClick={() => setAdding(false)}>{L('Cancel', '取消')}</button>
      </div>
    </div> : <button className="wb-btn small" onClick={() => setAdding(true)}>{L('Add fleet', '添加机队')}</button>}
  </>;
}
