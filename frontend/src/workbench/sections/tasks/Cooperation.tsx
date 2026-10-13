import { useState } from 'react';
import { Leaf, pointer, under } from '../../../workbench/form';
import type { SectionProps } from '../../../workbench/sections/types';
import { operation } from '../../../workbench/sections/operations';
import type { Draft } from './model';
import { OpApply, StagedNumber, stagedNumberProblem } from './op-ui';
import { cooperationParams } from './ops';

const RESPONDER_ORDER = ['respond_now', 'request_handoff', 'finish_then_respond', 'report_only'];

/** "street-east" passes through; "20,40,0" becomes an ENU position — never a silent default. */
function parseAt(text: string): string | number[] {
  const parts = text.split(',').map(part => Number(part.trim()));
  if (parts.length === 3 && parts.every(value => Number.isFinite(value))) return parts;
  return text.trim();
}

/** Cooperation responsibilities go through C11 set_cooperation / remove_cooperation: the server owns
 *  the tasks.config.cooperation structure and its generated entities. Removal is previewed (dry run)
 *  and confirmed — the diff shows exactly which generated points/incidents disappear (T1-017). */
export function CooperationEditor({ config, engineName, draft, edit, focus, L, api, project, onProject, saveDraft, zh }: {
  config: Draft; engineName: string;
} & Pick<SectionProps, 'api' | 'project' | 'onProject' | 'saveDraft' | 'zh'> & { draft: SectionProps['draft']; edit: SectionProps['edit']; focus?: string; L: SectionProps['L']; replaceDraft: SectionProps['replaceDraft'] }) {
  const base = pointer('engines', engineName, 'config', 'cooperation');
  const coop = config.cooperation as Draft | undefined;
  const vehicles = ((Array.isArray(draft.entities) ? draft.entities : []) as Draft[])
    .filter(entity => entity?.type === 'lowalt:UAV' || entity?.facts?.['lowalt.soc'] !== undefined)
    .map(entity => String(entity.id));
  const [watchers, setWatchers] = useState<string>(''), [helpers, setHelpers] = useState<string>(''), [responders, setResponders] = useState<string>('');
  const [radius, setRadius] = useState('150'), [askRadius, setAskRadius] = useState('500');
  const [responderMode, setResponderMode] = useState<'prescribed' | 'agent'>('prescribed');
  const [helperMode, setHelperMode] = useState<'prescribed' | 'agent'>('prescribed');
  const [incidentId, setIncidentId] = useState(''), [incidentAt, setIncidentAt] = useState(''), [incidentTime, setIncidentTime] = useState('90');
  const [staged, setStaged] = useState<Record<string, unknown> | undefined>();
  const [confirmRemove, setConfirmRemove] = useState(false);

  const multi = (label: string, value: string, setValue: (text: string) => void) => <label style={{ display: 'block', margin: '2px 0' }}>{label}
    <select className="wb-search" style={{ height: 28 }} multiple aria-label={label} value={value.split(',').filter(Boolean)}
      onChange={event => setValue(Array.from(event.target.selectedOptions).map(option => option.value).join(','))}>
      {vehicles.map(id => <option key={id} value={id}>{id}</option>)}
    </select>
    <span className="hint">{L('Hold Ctrl/Cmd to pick several; empty means no change to the current list.', '按住 Ctrl/Cmd 可多选；留空表示不修改当前列表。')}</span>
  </label>;

  if (!coop) return <div>
    <p className="hint">{L('No cooperation configured: when someone needs help, nobody is assigned.', '未配置协同：出现需要帮助的情况时不会有人被指派。')}</p>
    {vehicles.length === 0 && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L('Add UAVs (e.g. via Objects or a fleet) first — cooperation needs vehicles to assign.', '请先添加无人机（可在“对象”或机群中）——协同需要可指派的无人机。')}</p>}
    <div style={{ display: 'grid', gap: 6 }}>
      {multi(L('Watchers (who notices)', '观察者（谁发现）'), watchers, setWatchers)}
      <StagedNumber label={`${L('Notice radius', '发现半径')} (m)`} value={radius} min={0} onChange={setRadius} />
      {multi(L('Helpers (who assists)', '协助者（谁帮忙）'), helpers, setHelpers)}
      <StagedNumber label={`${L('Ask radius', '请求半径')} (m)`} value={askRadius} min={0} onChange={setAskRadius} />
    </div>
    <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
      operations={watchers || helpers ? [operation('set_cooperation', cooperationParams({
        ...(watchers ? { watchers: { vehicles: watchers.split(',').filter(Boolean), notice_radius_m: Number(radius) } } : {}),
        ...(helpers ? { helpers: { vehicles: helpers.split(',').filter(Boolean), ask_radius_m: Number(askRadius), mode: helperMode, selection: 'nearest' } } : {}),
      }))] : undefined}
      missing={!watchers && !helpers ? L('Pick at least one watcher or helper vehicle.', '请至少选择一名观察者或协助者。') : undefined}
      label={L('Set up cooperation', '配置协同')} labelZh="配置协同" primary small onApplied={() => { setWatchers(''); setHelpers(''); }} />
  </div>;

  const agentMode = coop.responder?.mode === 'agent' || coop.helpers?.mode === 'agent';
  const stagedOperations = staged ? [operation('set_cooperation', staged)] : undefined;
  const incidentTimeProblem = stagedNumberProblem(incidentTime, { min: 0, zh });
  const incidentReady = incidentId.trim() !== '' && incidentAt.trim() !== '' && !incidentTimeProblem;
  return <div className="wb-coop" data-path={base}>
    <p className="hint">{L('Who notices an incident, who decides the response and who helps; responsibilities are merged server-side through set_cooperation, and the run journals each handoff.', '谁发现事件、谁决策响应、谁去协助；责任通过 set_cooperation 由服务器合并，运行日志记录每次交接。')}</p>
    <details open><summary>{L('Watchers (who notices)', '观察者（谁发现）')}</summary>
      {multi(L('Watcher vehicles', '观察者无人机'), watchers, setWatchers)}      <StagedNumber label={`${L('Notice radius', '发现半径')} (m)`} value={radius} min={0} onChange={setRadius} />
    </details>
    <details open><summary>{L('Responder (who decides)', '响应者（谁决策）')}</summary>
      <label>{L('Mode', '模式')} <select value={responderMode} onChange={event => setResponderMode(event.target.value as 'prescribed' | 'agent')}>
        <option value="prescribed">{L('Prescribed (fixed rule)', '预设（固定规则）')}</option>
        <option value="agent">{L('Agent (live model)', '智能体（实时模型）')}</option>
      </select></label>
      {responderMode === 'agent' && <p className="hint">{L('Agent mode needs a connected model; the server rejects the operation without one.', '智能体模式需要已连接的模型；没有时服务器会拒绝该操作。')}</p>}
      {multi(L('Responder vehicles', '响应者无人机'), responders, setResponders)}
    </details>
    <details open><summary>{L('Helpers (who assists)', '协助者（谁帮忙）')}</summary>
      {multi(L('Helper vehicles', '协助者无人机'), helpers, setHelpers)}
      <StagedNumber label={`${L('Ask radius', '请求半径')} (m)`} value={askRadius} min={0} onChange={setAskRadius} />
      <label>{L('Mode', '模式')} <select value={helperMode} onChange={event => setHelperMode(event.target.value as 'prescribed' | 'agent')}>
        <option value="prescribed">{L('Prescribed (fixed rule)', '预设（固定规则）')}</option>
        <option value="agent">{L('Agent (live model)', '智能体（实时模型）')}</option>
      </select></label>
    </details>
    <div>
      <button className="wb-btn small" disabled={(!watchers && !helpers && !responders && responderMode === 'prescribed' && helperMode === 'prescribed' && Number(radius) === 150 && Number(askRadius) === 500)}
        onClick={() => setStaged(cooperationParams({
          ...(watchers ? { watchers: { vehicles: watchers.split(',').filter(Boolean), notice_radius_m: Number(radius) } } : {}),
          ...(responders || responderMode === 'agent' ? { responder: { mode: responderMode } } : {}),
          ...(helpers || helperMode === 'agent' ? { helpers: { ...(helpers ? { vehicles: helpers.split(',').filter(Boolean), ask_radius_m: Number(askRadius) } : {}), mode: helperMode } } : {}),
        }))}>{L('Stage responsibility change', '暂存责任修改')}</button>
      <p className="hint">{L('Only the parts you picked above are sent; everything else keeps its current value (the server merges).', '只会提交上面选择的部分；其余保持当前值（由服务器合并）。')}</p>
    </div>
    <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
      operations={stagedOperations}
      label={L('Update cooperation', '更新协同')} labelZh="更新协同" primary small onApplied={() => setStaged(undefined)} />
    <details><summary>{L('Incidents', '事件')}</summary>
      <p className="hint">{L('A scheduled point incident (person falling) with a place or ENU position and a time in seconds.', '安排一个点状事件（行人跌倒），带地点或 ENU 坐标与以秒计的时间。')}</p>
      {(Array.isArray(coop.incidents) ? coop.incidents as Draft[] : []).map((incident, index) => <div key={index} className="wb-gust-row" data-path={under(base, 'incidents', index)}>
        <span className="wb-mono">{String(incident.id ?? index)}</span>
        <span className="wb-tag">{String(incident.kind ?? '?')}</span>
        <span className="wb-tag">{incident.at_s !== undefined ? `${incident.at_s} s` : L('no time', '无时间')}</span>
        <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
          operations={[operation('remove_incident', { id: String(incident.id) })]}
          label={L('Remove', '移除')} labelZh="移除" danger small />
      </div>)}
      <div style={{ display: 'grid', gap: 6, marginTop: 6 }}>
        <input className="wb-search" style={{ height: 28 }} aria-label={L('Incident ID', '事件标识')} placeholder="fall-east" value={incidentId} onChange={event => setIncidentId(event.target.value)} />
        <label>{L('At (place id or east,north,up metres)', '位置（地点标识或东西,南北,垂直 米）')}
          <input className="wb-search" style={{ height: 28 }} aria-label={L('Incident position', '事件位置')} placeholder="street-east 或 20,40,0" value={incidentAt} onChange={event => setIncidentAt(event.target.value)} /></label>
        <StagedNumber label={`${L('Time', '时间')} (s)`} value={incidentTime} min={0} onChange={setIncidentTime} />
        {incidentTimeProblem && <p role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{incidentTimeProblem}</p>}
        {!incidentId.trim() && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L('Give the incident an ID and a place first.', '请先填写事件标识与位置。')}</p>}
      </div>
      {incidentReady && <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
        operations={[operation('add_incident', { id: incidentId.trim(), kind: 'person_fall', at: parseAt(incidentAt), time_s: Number(incidentTime) })]}
        label={L('Schedule incident', '安排事件')} labelZh="安排事件" primary small onApplied={() => { setIncidentId(''); setIncidentAt(''); }} />}
    </details>
    {agentMode && 'provider' in coop && <p className="hint">{L('A decision provider is pinned; it is added by the server when confirming agent mode.', '已固定决策提供方；确认智能体模式时由服务器添加。')}</p>}
    {confirmRemove
      ? <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
          operations={[operation('remove_cooperation', {})]}
          label={L('Confirm removal', '确认移除')} labelZh="确认移除" danger
          summary={<p className="hint" role="status">{L('Removing cooperation also removes its generated handoff points, incidents and handoff entities. The dry run above lists exactly what disappears.', '移除协同会同时删除其生成的交接点、事件与交接实体。上方预览会精确列出消失的内容。')}</p>}
          onApplied={() => setConfirmRemove(false)} />
      : <button className="wb-btn small danger" onClick={() => setConfirmRemove(true)}>{L('Remove cooperation…', '移除协同…')}</button>}
    {focus && <p className="hint wb-mono" style={{ fontSize: 11 }}>{base}</p>}
  </div>;
}
