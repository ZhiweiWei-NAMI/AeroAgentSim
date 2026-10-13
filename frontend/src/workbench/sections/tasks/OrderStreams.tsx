import { Leaf, pointer, under, TreeForm } from '../../../workbench/form';
import type { SectionProps } from '../../../workbench/sections/types';
import type { Draft } from './model';
import { newOrderStream, nsToS, siteIds, TASK_KINDS, type TaskKind } from './model';
import { useState } from 'react';

interface RowProps {
  stream: Draft; index: number; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'];
  onRemove: () => void;
}

/** Numeric leaf kept simple; the label carries the unit so users see s / 1/s / kg. */
function Num({ path, label, value, unit, onChange, focus }: { path: string; label: string; value: unknown; unit?: string; onChange: SectionProps['edit']; focus?: string }) {
  return <Leaf path={path} label={unit ? `${label} (${unit})` : label} value={value} onChange={onChange} focus={focus} />;
}

function ReleaseEditor({ stream, base, edit, focus, L }: { stream: Draft; base: string; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'] }) {
  const arrivals = (stream.arrivals ?? {}) as Draft;
  const mode = String(arrivals.mode ?? 'poisson');
  const set = (key: string, value: unknown) => edit(under(base, 'arrivals', key), value);
  return <fieldset><legend>{L('Arrivals', '到达')}</legend>
    <label>{L('Mode', '模式')} <select value={mode} onChange={event => {
      const next = event.target.value;
      if (next === 'poisson') { edit(under(base, 'arrivals'), { mode: 'poisson', rate_per_s: 0.05, start_ns: 0, end_ns: arrivals.end_ns ?? 180000000000 }); }
      else { const times = Array.isArray(arrivals.times_ns) ? arrivals.times_ns : [0]; edit(under(base, 'arrivals'), { mode: 'scheduled', times_ns: times }); }
    }}><option value="poisson">{L('Rate (Poisson)', '按速率（泊松）')}</option><option value="scheduled">{L('Scheduled times', '按时间表')}</option></select></label>
    {mode === 'poisson'
      ? <><Num path={under(base, 'arrivals', 'rate_per_s')} label={L('Release rate', '释放速率')} unit="1/s" value={arrivals.rate_per_s} onChange={edit} focus={focus} />
        <Num path={under(base, 'arrivals', 'start_ns')} label={L('Start (ns)', '开始（纳秒）')} value={arrivals.start_ns} onChange={edit} focus={focus} />
        <Num path={under(base, 'arrivals', 'end_ns')} label={L('End (ns)', '结束（纳秒）')} value={arrivals.end_ns} onChange={edit} focus={focus} />
        {nsToS(arrivals.start_ns) !== undefined && nsToS(arrivals.end_ns) !== undefined && <p className="hint">{L(`Window ${nsToS(arrivals.start_ns)}–${nsToS(arrivals.end_ns)} s`, `时间窗 ${nsToS(arrivals.start_ns)}–${nsToS(arrivals.end_ns)} 秒`)}</p>}
        {'burst_size' in arrivals && <Num path={under(base, 'arrivals', 'burst_size')} label={L('Burst size', '批量大小')} value={arrivals.burst_size} onChange={edit} focus={focus} />}</>
      : <p className="hint">{L('Release times stay in nanoseconds (times_ns) so the run is exact. One nondecreasing time per task.', '释放时间以纳秒（times_ns）保存以保证精确。每个任务一个非递减时间。')}</p>}
    {mode === 'scheduled' && <TreeForm value={arrivals.times_ns ?? []} path={under(base, 'arrivals', 'times_ns')} label="times_ns" onChange={edit} focus={focus} depth={2} />}
  </fieldset>;
}

export function OrderStreamRow({ stream, index, draft, edit, focus, L, onRemove }: RowProps) {
  const [expanded, setExpanded] = useState(false);
  const endpoints = siteIds(draft);
  const kinds = [...TASK_KINDS];
  const kind = String(stream.kind ?? '');
  const knownKind = (kinds as string[]).includes(kind);
  const release = stream.arrivals as Draft | undefined;
  const rate = release?.mode === 'poisson' ? release.rate_per_s : undefined;
  const scheduled = Array.isArray(release?.times_ns) ? release.times_ns.length : undefined;
  return <div className="wb-order-row" data-path={`/engines/tasks/config/generators/${index}`}>
    <button className="wb-row-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      <span className="wb-mono">{String(stream.id ?? `#${index}`)}</span>
      <span className="wb-tag">{knownKind ? kind : L('unknown kind', '未知类型')}</span>
      <span className="wb-tag">{scheduled !== undefined ? `${scheduled} ${L('scheduled', '定时')}` : rate !== undefined ? `${rate}/s` : L('no arrivals', '无到达')}</span>
      <span aria-hidden>{expanded ? '▾' : '▸'}</span>
    </button>
    {expanded && <div className="wb-order-body">
      <Leaf path={under('/engines/tasks/config/generators', index, 'id')} label={L('ID', '标识')} value={stream.id} onChange={edit} focus={focus} />
      <label>{L('Kind', '任务类型')} <select value={kind} onChange={event => edit(under('/engines/tasks/config/generators', index, 'kind'), event.target.value)}>
        {knownKind ? <option value={kind}>{kind}</option> : <option value="">{L('Unsupported — kept as authored', '不支持——保留原样')}</option>}
        {kinds.filter(item => item !== kind).map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      {!knownKind && <p className="hint">{L(`"${kind}" is not a known lowalt task kind; the server validates it.`, `“${kind}”不是已知的低空任务类型；由服务器校验。`)}</p>}
      {Array.isArray(stream.tasks) && <p className="hint">{L(`${stream.tasks.length} tasks`, `${stream.tasks.length} 个任务`)}</p>}
      <div>
        <p className="hint">{L('Origins (facilities)', '起点（设施）')}</p>
        <TreeForm value={stream.origins ?? []} path={under('/engines/tasks/config/generators', index, 'origins')} label="origins" onChange={edit} focus={focus} depth={2} />
        <datalist id={`order-origins-${index}`}>{endpoints.map(id => <option key={id} value={id} />)}</datalist>
      </div>
      <div>
        <p className="hint">{L('Destinations (facilities)', '终点（设施）')}</p>
        <TreeForm value={stream.destinations ?? []} path={under('/engines/tasks/config/generators', index, 'destinations')} label="destinations" onChange={edit} focus={focus} depth={2} />
        <datalist id={`order-destinations-${index}`}>{endpoints.map(id => <option key={id} value={id} />)}</datalist>
      </div>
      <ReleaseEditor stream={stream} base={under('/engines/tasks/config/generators', index)} edit={edit} focus={focus} L={L} />
      <Num path={under('/engines/tasks/config/generators', index, 'priority')} label={L('Priority', '优先级')} value={stream.priority} onChange={edit} focus={focus} />
      <Num path={under('/engines/tasks/config/generators', index, 'payload_kg')} label={L('Payload', '载荷')} unit="kg" value={stream.payload_kg} onChange={edit} focus={focus} />
      {kind === 'food' && <Num path={under('/engines/tasks/config/generators', index, 'delivery_window_s')} label={L('Delivery window', '配送时间窗')} unit="s" value={stream.delivery_window_s} onChange={edit} focus={focus} />}
      {Object.keys(stream).filter(key => !['id', 'kind', 'tasks', 'origins', 'destinations', 'arrivals', 'priority', 'payload_kg', 'delivery_window_s'].includes(key)).length > 0 && (
        <details><summary>{L('Other fields', '其他字段')}</summary><TreeForm value={Object.fromEntries(Object.entries(stream).filter(([key]) => !['id', 'kind', 'tasks', 'origins', 'destinations', 'arrivals', 'priority', 'payload_kg', 'delivery_window_s'].includes(key)))} path={under('/engines/tasks/config/generators', index)} onChange={edit} focus={focus} depth={1} /></details>)}
      <button className="wb-btn small danger" onClick={onRemove}>{L('Remove', '移除')}</button>
    </div>}
  </div>;
}

export function OrderStreams({ config, draft, edit, focus, L }: { config: Draft } & Pick<SectionProps, 'draft' | 'edit' | 'focus' | 'L'>) {
  const generators = Array.isArray(config.generators) ? config.generators : undefined;
  const [id, setId] = useState(''), [kind, setKind] = useState<string>('parcel'), [count, setCount] = useState(3);
  if (!generators) return <p className="hint">{L('This scenario has no task generators array in the tasks engine.', '该场景的 tasks 引擎没有任务生成器数组。')}</p>;
  const until_ns = Number((draft.run as Draft | undefined)?.until_ns ?? 0);
  const add = () => {
    if (!id || generators.some(stream => (stream as Draft)?.id === id)) return;
    edit(under('/engines/tasks/config/generators', generators.length), newOrderStream(id, kind as TaskKind, count, until_ns));
    setId('');
  };
  const remove = (index: number) => edit('/engines/tasks/config/generators', generators.filter((_, i) => i !== index));
  return <div>
    <div className="wb-order-add">
      <input aria-label={L('New order stream ID', '新订单流标识')} placeholder="parcel-rush" value={id} onChange={event => setId(event.target.value)} />
      <select aria-label={L('New order kind', '新订单类型')} value={kind} onChange={event => setKind(event.target.value)}>{TASK_KINDS.map(item => <option key={item} value={item}>{item}</option>)}</select>
      <input aria-label={L('Task count', '任务数')} type="number" min={1} value={count} onChange={event => setCount(Math.max(1, Math.floor(Number(event.target.value)) || 1))} />
      <button className="wb-btn small" disabled={!id || generators.some(stream => (stream as Draft)?.id === id)} onClick={add}>Add order stream</button>
    </div>
    {!generators.length && <div className="wb-empty">{L('No order streams yet.', '还没有订单流。')}</div>}
    {generators.map((stream, index) => (stream && typeof stream === 'object' && !Array.isArray(stream)
      ? <OrderStreamRow key={index} stream={stream as Draft} index={index} draft={draft} edit={edit} focus={focus} L={L} onRemove={() => remove(index)} />
      : <p key={index} className="hint" role="alert">{L(`Generator #${index} is not an object; edit it in the engine tree or YAML.`, `生成器 #${index} 不是对象；请在引擎树或 YAML 中编辑。`)}</p>))}
  </div>;
}
