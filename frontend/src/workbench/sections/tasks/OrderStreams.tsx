import { useMemo, useState } from 'react';
import type { SectionProps } from '../types';
import { canonicalJson, operation } from '../operations';
import { nsToS, orderStreamId, scalarNumber, siteIds, type Draft } from './model';
import { OpApply, Prerequisite, StagedNumber, parseStagedNumber, stagedNumberProblem, type FormNavigation } from './op-ui';
import { addOrdersParams, TASK_KINDS_C11, taskKindLabel, type OrdersInput, type Release } from './ops';

type OperationProps = Pick<SectionProps, 'api' | 'project' | 'onProject' | 'saveDraft' | 'zh' | 'L'> & FormNavigation;
const numberText = (value: unknown): string => {
  const number = scalarNumber(value);
  return number !== undefined ? String(number) : value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value);
};
interface Fields {
  id: string; kind: string; count: string; from: string[]; to: string[];
  mode: string; rate: string; start: string; end: string; times: string; burst: string;
  priority: string; payload: string; window: string;
}

function initialFields(draft: Draft, stream?: Draft, identity?: string): Fields {
  const arrivals = stream?.arrivals as Draft | undefined;
  const rate = scalarNumber(arrivals?.rate_per_s);
  const scheduled = arrivals?.times_s ?? (Array.isArray(arrivals?.times_ns) ? arrivals.times_ns.map(nsToS) : undefined);
  return {
    id: stream ? identity ?? '' : '', kind: stream?.kind ?? 'parcel', count: stream ? numberText(stream.tasks?.length) : '3',
    from: Array.isArray(stream?.origins) ? stream.origins : [], to: Array.isArray(stream?.destinations) ? stream.destinations : [],
    mode: stream ? arrivals?.mode ?? '' : 'poisson', rate: stream ? rate === undefined ? numberText(arrivals?.rate_per_s) : String(rate * 60) : '3',
    start: stream ? numberText(arrivals?.start_s ?? nsToS(arrivals?.start_ns)) : '0',
    end: stream ? numberText(arrivals?.end_s ?? nsToS(arrivals?.end_ns)) : numberText(nsToS(draft.run?.until_ns)),
    times: Array.isArray(scheduled) ? scheduled.map(numberText).join('\n') : '', burst: numberText(arrivals?.burst_size),
    priority: stream ? numberText(stream.priority) : '1', payload: stream ? numberText(stream.payload_kg) : '1',
    window: stream ? numberText(stream.delivery_window_s) : '240',
  };
}

/** Both create and edit keep raw text until preview; low-altitude structures are written by C11. */
function OrdersForm({ draft, stream, identity, existingIds = [], ...props }: OperationProps & { draft: Draft; stream?: Draft; identity?: string; existingIds?: Array<string | undefined> }) {
  const { L, zh, goTo } = props;
  const [changes, setChanges] = useState<Partial<Fields>>({});
  const [touched, setTouched] = useState<Set<keyof Fields>>(() => new Set());
  const fields = { ...initialFields(draft, stream, identity), ...changes };
  const set = <K extends keyof Fields>(key: K, value: Fields[K]) => {
    setChanges(previous => ({ ...previous, [key]: value }));
    setTouched(previous => new Set([...previous, key]));
  };
  const places = siteIds(draft);
  const origins = fields.kind === 'food' ? ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[])
    .filter(entity => entity?.type === 'lowalt:Restaurant' && places.includes(entity.id)).map(entity => entity.id as string) : places;
  const idTaken = !stream && existingIds.includes(fields.id.trim());
  const problems = {
    count: stagedNumberProblem(fields.count, { min: 1, max: 1000, integer: true, zh }),
    priority: stagedNumberProblem(fields.priority, { min: 0, integer: true, allowEmpty: true, zh }),
    payload: stagedNumberProblem(fields.payload, { min: 0, allowEmpty: true, zh }),
    window: fields.kind === 'food' ? stagedNumberProblem(fields.window, { exclusiveMin: 0, allowEmpty: true, zh }) : undefined,
    rate: fields.mode === 'poisson' ? stagedNumberProblem(fields.rate, { exclusiveMin: 0, zh }) : undefined,
    start: fields.mode === 'poisson' ? stagedNumberProblem(fields.start, { min: 0, allowEmpty: !!stream, zh }) : undefined,
    end: fields.mode === 'poisson' ? stagedNumberProblem(fields.end, { exclusiveMin: 0, allowEmpty: !!stream, zh }) : undefined,
    burst: fields.mode === 'poisson' ? stagedNumberProblem(fields.burst, { min: 1, max: 1000, integer: true, allowEmpty: true, zh }) : undefined,
  };
  const scheduled = fields.times.split(/\r?\n|,/).map(parseStagedNumber);
  const timesProblem = fields.mode !== 'scheduled' ? undefined
    : scheduled.some(value => value === undefined || value < 0) ? L('Each release time must be a finite number of 0 s or more.', '每个释放时间必须是不小于 0 秒的有限数值。')
    : scheduled.length !== parseStagedNumber(fields.count) ? L('Enter one release time per task.', '请为每个任务填写一个释放时间。')
    : scheduled.some((value, index) => index > 0 && value! < scheduled[index - 1]!) ? L('Release times must be nondecreasing.', '释放时间必须按非递减顺序排列。') : undefined;
  const start = parseStagedNumber(fields.start), end = parseStagedNumber(fields.end);
  const windowProblem = fields.mode === 'poisson' && start !== undefined && end !== undefined && end <= start
    ? L('End must be after start.', '结束必须晚于开始。') : undefined;
  const missing = !places.length ? undefined
    : fields.kind === 'food' && !origins.length ? L('Meal orders need a kitchen. Add one in Objects.', '餐食订单需要厨房，请在“对象”中添加。')
    : !fields.from.length || !fields.to.length ? L('Pick a start and an end facility.', '请选择起点与终点设施。')
    : fields.from.some(id => !origins.includes(id)) || fields.to.some(id => !places.includes(id)) ? L('Choose existing facilities for every endpoint.', '请为每个起终点选择已有设施。')
    : !fields.id.trim() && touched.size > 0 ? L('Give this order stream an ID.', '请填写订单流标识。') : undefined;
  const invalid = !fields.id.trim() || idTaken || !places.length || !!missing || !TASK_KINDS_C11.includes(fields.kind as never)
    || Object.values(problems).some(Boolean) || !!timesProblem || !!windowProblem || !['scheduled', 'poisson'].includes(fields.mode);
  const operations = useMemo(() => {
    if (invalid) return undefined;
    const release: Release = fields.mode === 'scheduled' ? { mode: 'scheduled', times_s: scheduled as number[] }
      : { mode: 'poisson', rate_per_s: parseStagedNumber(fields.rate)! / 60,
          ...(start !== undefined ? { start_s: start } : {}), ...(end !== undefined ? { end_s: end } : {}),
          ...(fields.burst.trim() ? { burst_size: parseStagedNumber(fields.burst)! } : {}) };
    const input: OrdersInput = {
      id: fields.id.trim(), kind: fields.kind, count: parseStagedNumber(fields.count)!, from: fields.from, to: fields.to, release,
      ...(fields.priority.trim() ? { priority: parseStagedNumber(fields.priority)! } : {}),
      ...(fields.payload.trim() ? { payload_kg: parseStagedNumber(fields.payload)! } : {}),
      ...(fields.kind === 'food' && fields.window.trim() ? { delivery_window_s: parseStagedNumber(fields.window)! } : {}),
    };
    const params = addOrdersParams(input);
    if (stream) delete params.kind;
    return [operation(stream ? 'set_orders' : 'add_orders', params)];
  }, [invalid, canonicalJson(fields), !!stream]);
  const picker = (key: 'from' | 'to', label: string, options: string[]) => <label className="wb-form-field">{label}
    <select multiple={!!stream} aria-label={label} disabled={!options.length} value={stream ? fields[key] : fields[key][0] ?? ''}
      onChange={event => set(key, Array.from(event.target.selectedOptions).map(option => option.value).filter(Boolean))}>
      {!stream && <option value="">{L('Pick a facility…', '选择设施…')}</option>}
      {fields[key].filter(id => !options.includes(id)).map(id => <option key={id} value={id}>{id} ({L('unavailable', '不可用')})</option>)}
      {options.map(id => <option key={id} value={id}>{id}</option>)}
    </select>
  </label>;
  return <fieldset className="wb-form-card wb-order-add">
    <legend className="wb-form-card-title">{L(stream ? 'Edit order stream' : 'Add order stream', stream ? '编辑订单流' : '添加订单流')}</legend>
    <label className="wb-form-field">{L('Order stream ID', '订单流标识')}
      <input aria-label={L('New order stream ID', '新订单流标识')} readOnly={!!stream} placeholder={L('e.g. parcel-rush', '例如 parcel-rush')} value={fields.id} onChange={event => set('id', event.target.value)} />
      {touched.has('id') && (!fields.id.trim() || idTaken) && <span className="hint wb-field-error" role="alert">{idTaken ? L('This stream ID is already used.', '该订单流标识已被使用。') : L('Required.', '必填。')}</span>}
    </label>
    <div className="wb-form-grid">
      <label className="wb-form-field">{L('Kind', '任务类型')}
        <select aria-label={L('New order kind', '新订单类型')} value={fields.kind} disabled={!!stream} onChange={event => set('kind', event.target.value)}>
          {!TASK_KINDS_C11.includes(fields.kind as never) && <option value={fields.kind}>{L('Unsupported kind', '不支持的类型')}</option>}
          {TASK_KINDS_C11.map(kind => <option key={kind} value={kind}>{taskKindLabel(kind, L)}</option>)}
        </select>
      </label>
      <StagedNumber label={L('Task count', '任务数')} value={fields.count} min={1} max={1000} integer onChange={value => set('count', value)} />
    </div>
    <div className="wb-form-grid">
      {picker('from', L('From facility', '起点设施'), origins)}
      {picker('to', L('To facility', '终点设施'), places)}
    </div>
    {!places.length && <Prerequisite goTo={goTo} section="objects" focus="add-place" action={L('Add place', '添加设施')}>{L('No depot or customer places yet', '还没有仓库或客户设施')}</Prerequisite>}
    <label className="wb-form-field">{L('Release schedule', '释放时间表')}
      <select aria-label={L('Release mode', '释放模式')} value={fields.mode} onChange={event => set('mode', event.target.value)}>
        {!['scheduled', 'poisson'].includes(fields.mode) && <option value={fields.mode}>{L('Choose a release schedule', '请选择释放时间表')}</option>}
        <option value="poisson">{L('Rate (Poisson)', '按速率（泊松）')}</option><option value="scheduled">{L('Scheduled times', '按时间表')}</option>
      </select>
    </label>
    {fields.mode === 'poisson' ? <>
      <div className="wb-form-grid">
        <StagedNumber label={`${L('Rate', '速率')} (1/min)`} value={fields.rate} exclusiveMin={0} onChange={value => set('rate', value)} />
        <StagedNumber label={`${L('Start', '开始')} (s)`} value={fields.start} min={0} allowEmpty={!!stream} onChange={value => set('start', value)} />
        <StagedNumber label={`${L('End', '结束')} (s)`} value={fields.end} exclusiveMin={0} allowEmpty={!!stream} onChange={value => set('end', value)} />
      </div>
      {windowProblem && (touched.has('start') || touched.has('end')) && <p role="alert" className="hint wb-field-error">{windowProblem}</p>}
      {fields.burst !== '' && <StagedNumber label={L('Burst size', '批量大小')} value={fields.burst} min={1} max={1000} integer onChange={value => set('burst', value)} />}
    </> : fields.mode === 'scheduled' && <label className="wb-form-field">{L('Release times (s, one per task)', '释放时间（秒，每个任务一个）')}
      <textarea className="wb-json" aria-label={L('Release times in seconds, one per line', '释放时间（秒），每行一个')} value={fields.times} onChange={event => set('times', event.target.value)} />
      {timesProblem && touched.has('times') && <span role="alert" className="hint wb-field-error">{timesProblem}</span>}
    </label>}
    <div className="wb-form-grid">
      <StagedNumber label={L('Priority', '优先级')} value={fields.priority} min={0} integer allowEmpty onChange={value => set('priority', value)} />
      <StagedNumber label={`${L('Payload', '载荷')} (kg)`} value={fields.payload} min={0} allowEmpty onChange={value => set('payload', value)} />
    </div>
    {fields.kind === 'food' && <StagedNumber label={`${L('Delivery window', '配送时间窗')} (s)`} value={fields.window} exclusiveMin={0} allowEmpty onChange={value => set('window', value)} />}
    <OpApply {...props} operations={operations} missing={missing} label={L('Preview', '预览')} primary small
      onApplied={() => { setChanges({}); setTouched(new Set()); }} />
  </fieldset>;
}

export function OrderStreamRow({ stream, index, draft, identity, onRemove, ...props }: OperationProps & { stream: Draft; index: number; draft: Draft; identity?: string; onRemove: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const { L } = props;
  return <div className="wb-order-row" data-testid="wb-order-row">
    <button type="button" className="wb-row-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      <span className="wb-mono">{identity ?? L(`Stream ${index + 1}`, `订单流 ${index + 1}`)}</span>
      <span className="wb-tag">{taskKindLabel(String(stream.kind), L)}</span>
      {Array.isArray(stream.tasks) && <span className="wb-tag">{L(`${stream.tasks.length} tasks`, `${stream.tasks.length} 个任务`)}</span>}
      <span aria-hidden>{expanded ? '▾' : '▸'}</span>
    </button>
    {expanded && <div className="wb-order-body">
      {identity && <OrdersForm {...props} stream={stream} identity={identity} draft={draft} />}
      {!identity && <p role="note" className="hint wb-form-prerequisite">{L('This stream has no unique workload ID. Check its ID and task members in File (YAML).', '该订单流没有唯一的工作量标识，请在“文件”（YAML）中检查标识和任务成员。')}</p>}
      <button type="button" className="wb-btn small danger" disabled={!identity} onClick={onRemove}>{L('Remove stream…', '移除订单流…')}</button>
    </div>}
  </div>;
}

export function OrderStreams({ config, draft, edit: _edit, focus: _focus, ...props }: OperationProps & { config: Draft; draft: SectionProps['draft']; edit: SectionProps['edit']; focus?: string }) {
  const { L } = props;
  const generators = Array.isArray(config.generators) ? config.generators as Draft[] : undefined;
  const identities = generators?.map(stream => stream && typeof stream === 'object' && !Array.isArray(stream) ? orderStreamId(stream) : undefined) ?? [];
  const [confirming, setConfirming] = useState<{ id: string; index: number }>();
  const selected = confirming && identities[confirming.index] === confirming.id && identities.filter(id => id === confirming.id).length === 1 ? confirming : undefined;
  const removeOperations = useMemo(() => selected ? [operation('remove_orders', { id: selected.id })] : undefined, [selected?.id]);
  if (!generators) return <p className="hint">{L('No order streams are declared in this task configuration.', '此任务配置未声明订单流。')}</p>;
  return <div className="wb-form-section">
    <OrdersForm {...props} draft={draft} existingIds={identities} />
    {!generators.length && <div className="wb-empty">{L('No order streams yet.', '还没有订单流。')}</div>}
    {generators.map((stream, index) => {
      if (!stream || typeof stream !== 'object' || Array.isArray(stream)) return <p key={index} className="hint" role="alert">{L(`Stream ${index + 1} is invalid. Repair it in File (YAML).`, `订单流 ${index + 1} 无效，请在“文件”（YAML）中修复。`)}</p>;
      const id = identities[index];
      const unique = id && identities.filter(value => value === id).length === 1 ? id : undefined;
      return <div key={index}>
        <OrderStreamRow {...props} stream={stream} index={index} identity={unique} draft={draft}
          onRemove={() => { if (unique) setConfirming({ id: unique, index }); }} />
        {selected?.index === index && <OpApply {...props} operations={removeOperations} label={L('Preview removal', '预览移除')} danger small
          summary={<p className="hint">{L(`Review everything that will be removed from stream "${selected.id}".`, `请核对移除订单流“${selected.id}”时会删除的全部内容。`)}</p>}
          onApplied={() => setConfirming(undefined)} />}
      </div>;
    })}
  </div>;
}
