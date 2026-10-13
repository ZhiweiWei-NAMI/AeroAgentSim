import { useMemo, useState } from 'react';
import { pointer } from '../../form';
import type { SectionProps } from '../types';
import { canonicalJson, operation } from '../operations';
import { handoffPointIds, nsToS, scalarNumber, vehicleIds, type Draft } from './model';
import { OpApply, Prerequisite, StagedNumber, parseStagedNumber, stagedNumberProblem, type FormNavigation } from './op-ui';
import { cooperationParams } from './ops';
import { IncidentsBlock } from './Traffic';

interface Fields {
  watchers: string[]; helpers: string[]; handoffs: string[]; radius: string; askRadius: string;
  responderMode: 'prescribed' | 'agent'; helperMode: 'prescribed' | 'agent'; responderGoal: string; helperGoal: string;
  selection: 'nearest' | 'first';
}
const ids = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim() !== '') : [];
const text = (value: unknown, absentDefault: string) => value === undefined ? absentDefault : String(scalarNumber(value) ?? value);

/** Participant assignments and handoff places use the compiler's cooperation schema. */
export function CooperationEditor({ config, engineName, draft, edit: _edit, focus, replaceDraft: _replaceDraft, ...props }: {
  config: Draft; engineName: string; draft: SectionProps['draft']; edit: SectionProps['edit']; focus?: string; replaceDraft: SectionProps['replaceDraft'];
} & Pick<SectionProps, 'api' | 'project' | 'onProject' | 'saveDraft' | 'zh' | 'L'> & FormNavigation) {
  const { L, zh, onSection } = props;
  const base = pointer('engines', engineName, 'config', 'cooperation');
  const coop = config.cooperation as Draft | undefined;
  const vehicles = vehicleIds(draft), handoffs = handoffPointIds(draft, coop);
  const initial: Fields = {
    watchers: ids(coop?.watchers?.vehicles), helpers: ids(coop?.helpers?.vehicles),
    handoffs: Array.isArray(coop?.handoff_points) ? coop.handoff_points.map((point: unknown) => typeof point === 'string' ? point : (point as Draft)?.id).filter((id: unknown): id is string => typeof id === 'string' && !!id.trim()) : [],
    radius: text(coop?.watchers?.notice_radius_m, '150'), askRadius: text(coop?.helpers?.ask_radius_m, '500'),
    responderMode: coop?.responder?.mode ?? 'prescribed', helperMode: coop?.helpers?.mode ?? 'prescribed',
    responderGoal: coop?.responder?.goal ?? '', helperGoal: coop?.helpers?.goal ?? '', selection: coop?.helpers?.selection ?? 'nearest',
  };
  const [changes, setChanges] = useState<Partial<Fields>>({});
  const fields = { ...initial, ...changes };
  const set = <K extends keyof Fields>(key: K, value: Fields[K]) => setChanges(previous => ({ ...previous, [key]: value }));
  const radiusProblem = stagedNumberProblem(fields.radius, { exclusiveMin: 0, zh });
  const askProblem = stagedNumberProblem(fields.askRadius, { exclusiveMin: 0, zh });
  const emptyAssignment = (changes.watchers !== undefined && !changes.watchers.length && initial.watchers.length > 0)
    || (changes.helpers !== undefined && !changes.helpers.length && initial.helpers.length > 0);
  const invalid = !!radiusProblem || !!askProblem || fields.watchers.some(id => !vehicles.includes(id)) || fields.helpers.some(id => !vehicles.includes(id))
    || fields.handoffs.some(id => !handoffs.includes(id)) || emptyAssignment
    || !['prescribed', 'agent'].includes(fields.responderMode) || !['prescribed', 'agent'].includes(fields.helperMode) || !['nearest', 'first'].includes(fields.selection)
    || (fields.responderMode === 'agent' && !fields.responderGoal.trim()) || (fields.helperMode === 'agent' && !fields.helperGoal.trim());
  const missing = !vehicles.length ? L('Add UAVs in Objects before assigning cooperation.', '请先在“对象”中添加无人机，再指派协同。')
    : !coop && !fields.watchers.length && !fields.helpers.length ? L('Pick at least one watcher or helper vehicle.', '请至少选择一名观察者或协助者。')
    : emptyAssignment ? L('Choose at least one vehicle for each assigned group.', '请为每个已指派的组至少选择一架无人机。')
    : fields.helperMode === 'agent' && !fields.helpers.length ? L('Select helper vehicles before choosing agent mode.', '选择智能体模式前请先指派协助者无人机。')
    : fields.responderMode === 'agent' && !fields.responderGoal.trim() || fields.helperMode === 'agent' && !fields.helperGoal.trim()
      ? L('Agent mode needs a goal and a connected model.', '智能体模式需要目标和已连接的模型。') : undefined;
  const operations = useMemo(() => {
    if (invalid || missing || (coop && !Object.keys(changes).length)) return undefined;
    const params = cooperationParams({
      ...(fields.watchers.length ? { watchers: { vehicles: fields.watchers, notice_radius_m: parseStagedNumber(fields.radius)! } } : {}),
      ...(coop || fields.responderMode === 'agent' ? { responder: { mode: fields.responderMode, ...(fields.responderGoal.trim() ? { goal: fields.responderGoal.trim() } : {}) } } : {}),
      ...(fields.helpers.length ? { helpers: { vehicles: fields.helpers, ask_radius_m: parseStagedNumber(fields.askRadius)!, mode: fields.helperMode, selection: fields.selection, ...(fields.helperGoal.trim() ? { goal: fields.helperGoal.trim() } : {}) } } : {}),
      ...(changes.handoffs !== undefined || !coop && fields.handoffs.length ? { handoff_points: fields.handoffs } : {}),
    });
    return [operation('set_cooperation', params)];
  }, [invalid, missing, canonicalJson(fields), changes.handoffs !== undefined, Object.keys(changes).length, !!coop]);
  const removeOperations = useMemo(() => [operation('remove_cooperation', {})], []);
  const multi = (key: 'watchers' | 'helpers' | 'handoffs', label: string, options: string[]) => <label className="wb-form-field">{label}
    <select className="wb-search" multiple disabled={!options.length} aria-label={label} value={fields[key]}
      onChange={event => set(key, Array.from(event.target.selectedOptions).map(option => option.value))}>
      {fields[key].filter(id => !options.includes(id)).map(id => <option key={id} value={id}>{id} ({L('unavailable', '不可用')})</option>)}
      {options.map(id => <option key={id} value={id}>{id}</option>)}
    </select>
    <span className="hint">{L('Hold Ctrl/Cmd to select several.', '按住 Ctrl/Cmd 可多选。')}</span>
  </label>;
  const mode = (key: 'responderMode' | 'helperMode', label: string) => <label className="wb-form-field">{label}
    <select className="wb-search" aria-label={label} value={fields[key]} onChange={event => set(key, event.target.value as Fields[typeof key])}>
      {!['prescribed', 'agent'].includes(fields[key]) && <option value={fields[key]}>{L('Unsupported mode', '不支持的模式')}</option>}
      <option value="prescribed">{L('Fixed rules', '固定规则')}</option><option value="agent">{L('Agent', '智能体')}</option>
    </select>
  </label>;
  const goal = (key: 'responderGoal' | 'helperGoal', label: string) => <label className="wb-form-field">{label}
    <input className="wb-search" aria-label={label} value={fields[key]} onChange={event => set(key, event.target.value)} />
  </label>;
  return <div className="wb-coop wb-form-section" data-path={base}>
    <p className="hint">{L('Assign who notices an incident, who decides the response, and who helps at a handoff point.', '指派事件观察者、响应决策者，以及在交接点提供帮助的无人机。')}</p>
    <div className="wb-form-stack">
      {!vehicles.length && <Prerequisite onSection={onSection} section="objects" action={L('Add fleet', '添加机群')}>{L('No UAVs yet — add a fleet in Objects.', '还没有无人机——请在“对象”中添加机群。')}</Prerequisite>}
      {multi('watchers', L('Watcher vehicles', '观察者无人机'), vehicles)}
      <StagedNumber label={`${L('Notice radius', '发现半径')} (m)`} value={fields.radius} exclusiveMin={0} disabled={!fields.watchers.length} onChange={value => set('radius', value)} />
      {mode('responderMode', L('Response mode', '响应模式'))}
      {fields.responderMode === 'agent' && goal('responderGoal', L('Response goal', '响应目标'))}
      {multi('helpers', L('Helper vehicles', '协助者无人机'), vehicles)}
      <StagedNumber label={`${L('Ask radius', '请求半径')} (m)`} value={fields.askRadius} exclusiveMin={0} disabled={!fields.helpers.length} onChange={value => set('askRadius', value)} />
      {mode('helperMode', L('Helper mode', '协助模式'))}
      {fields.helperMode === 'agent' && goal('helperGoal', L('Helper goal', '协助目标'))}
      <label className="wb-form-field">{L('Helper selection', '协助者选择')}
        <select className="wb-search" aria-label={L('Helper selection', '协助者选择')} value={fields.selection} onChange={event => set('selection', event.target.value as Fields['selection'])}>
          {!['nearest', 'first'].includes(fields.selection) && <option value={fields.selection}>{L('Unsupported selection', '不支持的选择方式')}</option>}
          <option value="nearest">{L('Nearest', '最近')}</option><option value="first">{L('First available', '首个可用')}</option>
        </select>
      </label>
      {multi('handoffs', L('Handoff points', '交接点'), handoffs)}
      {!handoffs.length && <Prerequisite onSection={onSection} section="objects:add-place" action={L('Add handoff point', '添加交接点')}>{L('No handoff-point places yet — add one in Objects → Add place.', '还没有交接点设施——请在“对象”→“添加设施”中添加。')}</Prerequisite>}
      <OpApply {...props} operations={operations} missing={missing} label={L('Preview', '预览')} primary small onApplied={() => setChanges({})} />
    </div>
    {coop && <>
      <details><summary>{L('Incidents', '事件')}</summary>
        <IncidentsBlock config={config} duration_s={nsToS((draft.run as Draft | undefined)?.until_ns)} {...props} />
      </details>
      <OpApply {...props} operations={removeOperations} label={L('Preview cooperation removal', '预览移除协同')} danger small
        summary={<p className="hint">{L('Review the cooperation assignments, handoff points and incidents that will be removed.', '请核对将被移除的协同指派、交接点与事件。')}</p>} />
    </>}
    {focus && <p className="hint wb-mono">{base}</p>}
  </div>;
}
