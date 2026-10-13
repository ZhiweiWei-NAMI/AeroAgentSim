import { useState } from 'react';
import { Leaf, under } from '../../../workbench/form';
import type { SectionProps } from '../../../workbench/sections/types';
import { operation } from '../../../workbench/sections/operations';
import type { Draft } from './model';
import { nsToS } from './model';
import { OpApply, StagedNumber, stagedNumberProblem } from './op-ui';
import { addOrdersParams, type OrdersInput, type Release } from './ops';

const KINDS = ['parcel', 'food', 'medical', 'inspection', 'survey', 'spraying', 'passenger', 'patrol', 'incident_response'];
const SECOND = 1e9;

interface RowProps {
  stream: Draft; index: number; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'];
  onRemove: () => void;
}

/** Facility picker backed by existing places; the current value stays visible even if unknown. */
function Picker({ label, ids, value, onChange, L }: { label: string; ids: string[]; value: unknown; onChange: (value: unknown) => void; L: SectionProps['L'] }) {
  const list = Array.isArray(value) ? value as unknown[] : [];
  return <div style={{ margin: '4px 0' }}>
    <p className="hint" style={{ margin: '2px 0' }}>{label}</p>
    {list.map((item, index) => <div key={index} style={{ display: 'flex', gap: 6, margin: '2px 0' }}>
      <select aria-label={`${label} ${index + 1}`} value={String(item)} onChange={event => onChange(list.map((v, i) => i === index ? event.target.value : v))}>
        {!ids.includes(String(item)) && <option value={String(item)}>{String(item)} ({L('unknown', '未知')})</option>}
        {ids.map(id => <option key={id} value={id}>{id}</option>)}
      </select>
      <button type="button" className="wb-btn small danger" aria-label={`✕ ${label} ${index + 1}`} onClick={() => onChange(list.filter((_, i) => i !== index))}>✕</button>
    </div>)}
    {ids.length > 0 && <button type="button" className="wb-btn small" onClick={() => onChange([...list, ids[0]])}>+ {label}</button>}
  </div>;
}

/** Release schedule shown in SECONDS; the caller stores the exact nanosecond shape. */
function ReleaseEditor({ release, onChange, L }: { release: Draft; onChange: (value: Draft) => void; L: SectionProps['L'] }) {
  const mode = release.mode === 'scheduled' ? 'scheduled' : 'poisson';
  const set = (key: string, value: unknown) => onChange({ ...release, [key]: value });
  const times = Array.isArray(release.times_s) ? (release.times_s as unknown[]).map(Number) : [];
  const timesBad = times.some(value => !Number.isFinite(value) || value < 0);
  const windowBad = mode === 'poisson' && Number.isFinite(Number(release.end_s)) && Number.isFinite(Number(release.start_s ?? 0)) && Number(release.end_s) <= Number(release.start_s ?? 0);
  return <fieldset><legend>{L('Release schedule (seconds)', '释放时间表（秒）')}</legend>
    <label>{L('Mode', '模式')} <select value={mode} onChange={event => {
      onChange(event.target.value === 'scheduled'
        ? { mode: 'scheduled', times_s: times.length ? times : [0] }
        : { mode: 'poisson', rate_per_s: 3, start_s: 0, end_s: 180 });
    }}>{['poisson', 'scheduled'].map(item => <option key={item} value={item}>{item === 'poisson' ? L('Rate (Poisson)', '按速率（泊松）') : L('Scheduled times', '按时间表')}</option>)}</select></label>
    {mode === 'poisson' ? <>
      <StagedNumber label={`${L('Rate', '速率')} (1/min)`} value={release.rate_per_s === undefined || !Number.isFinite(Number(release.rate_per_s)) ? '' : String(Number(release.rate_per_s) * 60)} min={0} onChange={text => {
        const parsed = Number(text);
        set('rate_per_s', text.trim() === '' || !Number.isFinite(parsed) ? undefined : parsed / 60);
      }} />
      <StagedNumber label={`${L('Start', '开始')} (s)`} value={release.start_s === undefined ? '' : String(release.start_s)} min={0} onChange={text => set('start_s', text.trim() === '' ? undefined : Number(text))} />
      <StagedNumber label={`${L('End', '结束')} (s)`} value={release.end_s === undefined ? '' : String(release.end_s)} min={0} onChange={text => set('end_s', text.trim() === '' ? undefined : Number(text))} />
      {windowBad && <p role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{L('End must be after start.', '结束必须晚于开始。')}</p>}
      {'burst_size' in release && <StagedNumber label={L('Burst size', '批量大小')} value={String(release.burst_size)} min={1} integer onChange={text => set('burst_size', Number(text))} />}
    </> : <>
      <p className="hint">{L('One release time in seconds per task, nondecreasing.', '每个任务一个释放时间（秒），非递减。')}</p>
      <textarea className="wb-json" style={{ minHeight: 60 }} aria-label={L('Release times in seconds, one per line', '释放时间（秒），每行一个')} value={times.join('\n')} onChange={event => {
        const lines = event.target.value.split('\n').map(line => line.trim()).filter(line => line !== '');
        set('times_s', lines.map(Number));
      }} />
      {timesBad && <p role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{L('Each time must be a finite number of 0 s or more.', '每个时间必须是不小于 0 秒的有限数值。')}</p>}
    </>}
  </fieldset>;
}

export function OrderStreamRow({ stream, index, draft, edit, focus, L, onRemove }: RowProps) {
  const [expanded, setExpanded] = useState(false);
  const places = ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[]).map(entity => String(entity?.id ?? '')).filter(Boolean);
  const kind = String(stream.kind ?? '');
  const knownKind = KINDS.includes(kind);
  const release = (stream.arrivals ?? {}) as Draft;
  // Stored arrivals may be nanoseconds or the C11 seconds shape; show seconds either way.
  const releaseSeconds: Draft = release.mode === 'scheduled'
    ? { mode: 'scheduled', times_s: Array.isArray(release.times_s) ? release.times_s : (Array.isArray(release.times_ns) ? (release.times_ns as unknown[]).map(value => nsToS(value)).map(Number) : []) }
    : {
        mode: 'poisson', rate_per_s: release.rate_per_s,
        start_s: release.start_s !== undefined ? nsToS(release.start_s) ?? release.start_s : undefined,
        end_s: release.end_s !== undefined ? nsToS(release.end_s) ?? release.end_s : undefined,
        ...(release.burst_size !== undefined ? { burst_size: release.burst_size } : {}),
      };
  const rate = release.mode === 'poisson' ? release.rate_per_s : undefined;
  const scheduled = Array.isArray(release.times_ns) ? release.times_ns.length : Array.isArray(release.times_s) ? release.times_s.length : undefined;
  const set = (key: string, value: unknown) => edit(under('/engines/tasks/config/generators', index, key), value);
  const setRelease = (value: Draft) => {
    // Author back in the stored shape: nanoseconds, converted exactly from the seconds shown.
    const next: Draft = value.mode === 'scheduled'
      ? { mode: 'scheduled', times_ns: (value.times_s as number[]).map(seconds => Math.round(seconds * SECOND)) }
      : {
          mode: 'poisson', rate_per_s: value.rate_per_s,
          start_ns: Math.round((value.start_s ?? 0) * SECOND), end_ns: Math.round((value.end_s ?? 0) * SECOND),
          ...(value.burst_size !== undefined ? { burst_size: value.burst_size } : {}),
        };
    set('arrivals', next);
  };
  return <div className="wb-order-row" data-path={`/engines/tasks/config/generators/${index}`}>
    <button className="wb-row-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      <span className="wb-mono">{String(stream.id ?? `#${index}`)}</span>
      <span className="wb-tag">{knownKind ? kind : L('unknown kind', '未知类型')}</span>
      <span className="wb-tag">{scheduled !== undefined ? `${scheduled} ${L('scheduled', '定时')}` : rate !== undefined ? `${rate}/s` : L('no arrivals', '无到达')}</span>
      <span aria-hidden>{expanded ? '▾' : '▸'}</span>
    </button>
    {expanded && <div className="wb-order-body">
      <Leaf path={under('/engines/tasks/config/generators', index, 'id')} label={L('ID', '标识')} value={stream.id} onChange={edit} focus={focus} />
      <label>{L('Kind', '任务类型')} <select value={kind} onChange={event => set('kind', event.target.value)}>
        {knownKind ? <option value={kind}>{kind}</option> : <option value={kind}>{L('Unsupported — kept as authored', '不支持——保留原样')}</option>}
        {KINDS.filter(item => item !== kind).map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      {!knownKind && <p className="hint">{L(`"${kind}" is not a known lowalt task kind; the server validates it.`, `“${kind}”不是已知的低空任务类型；由服务器校验。`)}</p>}
      {Array.isArray(stream.tasks) && <p className="hint">{L(`${stream.tasks.length} tasks`, `${stream.tasks.length} 个任务`)}</p>}
      <Picker label={L('From (facilities)', '起点（设施）')} ids={places} value={stream.origins ?? []} onChange={value => set('origins', value)} L={L} />
      <Picker label={L('To (facilities)', '终点（设施）')} ids={places} value={stream.destinations ?? []} onChange={value => set('destinations', value)} L={L} />
      {places.length === 0 && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L('No facilities exist yet — add a restaurant or depot in Objects first.', '还没有任何设施——请先在“对象”中添加餐厅或仓库。')}</p>}
      <ReleaseEditor release={releaseSeconds} onChange={setRelease} L={L} />
      <StagedNumber label={L('Priority', '优先级')} value={stream.priority === undefined ? '' : String(stream.priority)} min={0} integer allowEmpty onChange={text => set('priority', text.trim() === '' ? undefined : Number(text))} />
      <StagedNumber label={`${L('Payload', '载荷')} (kg)`} value={stream.payload_kg === undefined ? '' : String(stream.payload_kg)} min={0} allowEmpty onChange={text => set('payload_kg', text.trim() === '' ? undefined : Number(text))} />
      {kind === 'food' && <StagedNumber label={`${L('Delivery window', '配送时间窗')} (s)`} value={stream.delivery_window_s === undefined ? '' : String(stream.delivery_window_s)} min={0} allowEmpty onChange={text => set('delivery_window_s', text.trim() === '' ? undefined : Number(text))} />}
      {Object.keys(stream).filter(key => !['id', 'kind', 'tasks', 'origins', 'destinations', 'arrivals', 'priority', 'payload_kg', 'delivery_window_s'].includes(key)).length > 0 && (
        <details><summary>{L('Other fields', '其他字段')}</summary><p className="hint">{L('Edit extra fields in the engine tree or YAML so authored kinds stay exact.', '请在引擎树或 YAML 中编辑其他字段，以保持已写类型的精确。')}</p></details>)}
      <button className="wb-btn small danger" onClick={onRemove}>{L('Remove stream…', '移除订单流…')}</button>
    </div>}
  </div>;
}

/** Order streams: creation goes through C11 add_orders (preview → apply); removal dry-runs first (T1-017). */
export function OrderStreams({ config, draft, edit, focus, L, api, project, onProject, saveDraft, zh }: { config: Draft } & Pick<SectionProps, 'api' | 'project' | 'onProject' | 'saveDraft' | 'zh'> & { draft: SectionProps['draft']; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'] }) {
  const generators = Array.isArray(config.generators) ? config.generators as Draft[] : undefined;
  const places = ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[]).map(entity => String(entity?.id ?? '')).filter(Boolean);
  const [id, setId] = useState(''), [kind, setKind] = useState<string>('parcel'), [count, setCount] = useState('3');
  const [from, setFrom] = useState<string>(''), [to, setTo] = useState<string>('');
  const [release, setRelease] = useState<Release>({ mode: 'poisson', rate_per_s: 0.05, start_s: 0, end_s: 180 });
  const [priority, setPriority] = useState('1'), [payload, setPayload] = useState('1'), [window_, setWindow] = useState('240');
  const [staged, setStaged] = useState<OrdersInput | undefined>(), [confirming, setConfirming] = useState<string | undefined>();
  if (!generators) return <p className="hint">{L('This scenario has no task generators array in the tasks engine.', '该场景的 tasks 引擎没有任务生成器数组。')}</p>;

  // Staged validation: nothing is sent with a fabricated default.
  const idTaken = generators.some(stream => stream?.id === id);
  const countProblem = stagedNumberProblem(count, { min: 1, integer: true, zh });
  const rateProblem = release.mode === 'poisson'
    ? stagedNumberProblem(release.rate_per_s === undefined ? '' : String(release.rate_per_s), { min: 0, zh })
      ?? (Number(release.end_s ?? 0) <= Number(release.start_s ?? 0) ? L('End must be after start.', '结束必须晚于开始。') : undefined)
    : (release.times_s ?? []).some(value => !Number.isFinite(value) || value < 0) ? L('Each release time must be a finite number of 0 s or more.', '每个释放时间必须是不小于 0 秒的有限数值。') : undefined;
  const endpointsMissing = !from || !to;
  const missing = !places.length
    ? L('Add a facility (restaurant/depot) in Objects first.', '请先在“对象”中添加设施（餐厅/仓库）。')
    : idTaken ? L('This stream ID is already used.', '该订单流标识已被使用。')
    : countProblem ?? rateProblem ?? (endpointsMissing ? L('Pick a start and an end facility.', '请选择起点与终点设施。') : undefined);
  const build = (): OrdersInput | undefined => {
    if (missing) return undefined;
    const releaseValue: Release = release.mode === 'poisson'
      ? { mode: 'poisson', rate_per_s: Number(release.rate_per_s), start_s: Number(release.start_s ?? 0), end_s: Number(release.end_s) }
      : { mode: 'scheduled', times_s: (release.times_s ?? []).filter(value => Number.isFinite(value) && value >= 0) };
    return {
      id, kind, count: Number(count), from: [from], to: [to], release: releaseValue,
      ...(priority.trim() !== '' ? { priority: Number(priority) } : {}),
      ...(payload.trim() !== '' ? { payload_kg: Number(payload) } : {}),
      ...(kind === 'food' && window_.trim() !== '' ? { delivery_window_s: Number(window_) } : {}),
    };
  };
  const stage = () => { const built = build(); if (built) setStaged(built); };
  const stageOperations = staged ? [operation('add_orders', addOrdersParams(staged))] : undefined;
  const confirmingStream = confirming !== undefined ? generators.find(stream => stream?.id === confirming) : undefined;
  return <div>
    <div className="wb-order-add" style={{ display: 'grid', gap: 6 }}>
      <input className="wb-search" style={{ height: 28 }} aria-label={L('New order stream ID', '新订单流标识')} placeholder="parcel-rush" value={id} onChange={event => setId(event.target.value)} />
      <label>{L('Kind', '任务类型')} <select aria-label={L('New order kind', '新订单类型')} value={kind} onChange={event => setKind(event.target.value)}>
        {KINDS.map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      <StagedNumber label={L('Task count', '任务数')} value={count} min={1} integer onChange={setCount} />
      <label>{L('From', '起点')} <select aria-label={L('From facility', '起点设施')} value={from} onChange={event => setFrom(event.target.value)}>
        <option value="">{L('Pick a facility…', '选择设施…')}</option>
        {places.map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      <label>{L('To', '终点')} <select aria-label={L('To facility', '终点设施')} value={to} onChange={event => setTo(event.target.value)}>
        <option value="">{L('Pick a facility…', '选择设施…')}</option>
        {places.map(item => <option key={item} value={item}>{item}</option>)}
      </select></label>
      {places.length === 0 && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L('No facilities yet — add one in Objects first.', '还没有设施——请先在“对象”中添加。')}</p>}
      {release.mode === 'poisson' ? <>
        <StagedNumber label={`${L('Rate', '速率')} (1/min)`} value={release.rate_per_s === undefined ? '' : String(Number(release.rate_per_s) * 60)} min={0} onChange={text => {
          const parsed = Number(text);
          setRelease(previous => previous.mode === 'poisson'
            ? { ...previous, rate_per_s: text.trim() === '' || !Number.isFinite(parsed) ? undefined : parsed / 60 }
            : previous);
        }} />
        <StagedNumber label={`${L('Start', '开始')} (s)`} value={release.start_s === undefined ? '' : String(release.start_s)} min={0} onChange={text => setRelease(previous => previous.mode === 'poisson' ? { ...previous, start_s: text.trim() === '' ? undefined : Number(text) } : previous)} />
        <StagedNumber label={`${L('End', '结束')} (s)`} value={release.end_s === undefined ? '' : String(release.end_s)} min={0} onChange={text => setRelease(previous => previous.mode === 'poisson' ? { ...previous, end_s: text.trim() === '' ? undefined : Number(text) } : previous)} />
      </> : <StagedNumber label={L('Times (s, comma separated)', '时间（秒，逗号分隔）')} value={(release.times_s ?? []).join(', ')} onChange={text => setRelease({ mode: 'scheduled', times_s: text.split(',').map(part => Number(part.trim())).filter(value => Number.isFinite(value) && value >= 0) })} />}
      <StagedNumber label={L('Priority', '优先级')} value={priority} min={0} integer allowEmpty onChange={setPriority} />
      <StagedNumber label={`${L('Payload', '载荷')} (kg)`} value={payload} min={0} allowEmpty onChange={setPayload} />
      {kind === 'food' && <StagedNumber label={`${L('Delivery window', '配送时间窗')} (s)`} value={window_} min={0} allowEmpty onChange={setWindow} />}
      <button className="wb-btn small" disabled={!!missing || !!staged} onClick={stage}>{L('Stage order stream', '暂存订单流')}</button>
      {staged && <p className="hint" role="status">{L(`Staged "${staged.id}" — preview it below, then apply.`, `已暂存“${staged.id}”——请在下方预览后再应用。`)}</p>}
    </div>
    <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
      operations={stageOperations}
      missing={missing}
      label={L('Add order stream', '添加订单流')} labelZh="添加订单流" primary small
      onApplied={() => { setStaged(undefined); setId(''); }} />
    {!generators.length && <div className="wb-empty">{L('No order streams yet.', '还没有订单流。')}</div>}
    {generators.map((stream, index) => stream && typeof stream === 'object' && !Array.isArray(stream)
      ? <div key={index}>
          <OrderStreamRow stream={stream as Draft} index={index} draft={draft} edit={edit} focus={focus} L={L}
            onRemove={() => { setConfirming(String(stream.id ?? '')); setStaged(undefined); }} />
          {confirmingStream?.id === stream.id && <div style={{ border: '1px solid var(--wb-bad)', borderRadius: 10, padding: 10, margin: '6px 0' }}>
            <p className="hint" role="status">{L(`Removing stream "${stream.id}" removes its ${Array.isArray(stream.tasks) ? stream.tasks.length : 0} task(s) and nothing else — the server dry run below is exact.`, `移除订单流“${stream.id}”会删除其 ${Array.isArray(stream.tasks) ? stream.tasks.length : 0} 个任务，不影响其他内容——下方的服务器预览是精确的。`)}</p>
            <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
              operations={[operation('remove_orders', { id: String(stream.id) })]}
              label={L('Confirm removal', '确认移除')} labelZh="确认移除" danger small
              onApplied={() => setConfirming(undefined)} />
          </div>}
        </div>
      : <p key={index} className="hint" role="alert">{L(`Generator #${index} is not an object; edit it in the engine tree or YAML.`, `生成器 #${index} 不是对象；请在引擎树或 YAML 中编辑。`)}</p>)}
  </div>;
}
