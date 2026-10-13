import { useState } from 'react';
import type { SectionProps } from '../../../workbench/sections/types';
import { operation } from '../../../workbench/sections/operations';
import type { Draft } from './model';
import { OpApply, StagedNumber, stagedNumberProblem } from './op-ui';
import { Leaf, pointer, TreeForm } from '../../../workbench/form';

/** Traffic block: states the provenance honestly. SUMO (adapter 'sumo'), procedural road motion
 *  (traffic_road_motion with authored actors/routes) and render-only traffic are different things. */
export function TrafficBlock({ draft, edit, focus, L }: { draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'] }) {
  const engines = (draft.engines ?? {}) as Record<string, Draft>;
  const sumo = Object.entries(engines).find(([, engine]) => engine?.plugin === 'sumo');
  const procedural = Object.entries(engines).filter(([, engine]) => engine?.plugin === 'traffic_road_motion');
  const assessment = Object.entries(engines).filter(([, engine]) => engine?.plugin === 'traffic_assessment');
  return <div className="wb-traffic" data-testid="wb-traffic">
    {!sumo && !procedural.length && <div className="wb-empty">{L('No SUMO or road/traffic engine is configured in this scenario, so there is no ground traffic to configure — and if any appears, it is scripted or decorative, never a SUMO simulation.', '该场景没有配置 SUMO 或道路/交通引擎，因此没有可配置的地面交通——即使出现，也是脚本或装饰性的，绝不是 SUMO 仿真。')}</div>}
    {sumo && <details open><summary>SUMO <span className="wb-tag wb-mono">{sumo[0]}</span></summary>
      <p className="hint">{L('Real SUMO network traffic. Vehicle counts, arrival rates and routes are set in the SUMO configuration/network, not here; this draft only holds the engine contract.', '真实 SUMO 路网交通。车辆数量、到达率与路线在 SUMO 配置/路网中设置，不在这里；此草稿只保存引擎契约。')}</p>
      <TreeForm value={sumo[1].config ?? {}} path={pointer('engines', sumo[0], 'config')} onChange={edit} focus={focus} depth={1} />
    </details>}
    {procedural.map(([name, engine]) => <details key={name} open><summary>{L('Procedural road traffic', '程序化道路交通')} <span className="wb-tag wb-mono">{name}</span> <span className="wb-tag warn">{L('scripted, not SUMO', '脚本生成，非 SUMO')}</span></summary>
      <p className="hint">{L('Actors follow authored routes with authored speeds; this is traceable scripted traffic, not a SUMO simulation. Density or arrival-rate fields do not exist, so none are shown.', '参与者沿已编写的路线以已编写的速度行驶；这是可追溯的脚本交通，不是 SUMO 仿真。不存在密度或到达率字段，因此不展示。')}</p>
      <p className="hint">{L(`Actors: ${(Array.isArray(engine.config?.actors) ? engine.config.actors.length : 0)} · Routes: ${Array.isArray(engine.config?.routes) ? engine.config.routes.length : 0}`, `参与者：${Array.isArray(engine.config?.actors) ? engine.config.actors.length : 0} · 路线：${Array.isArray(engine.config?.routes) ? engine.config.routes.length : 0}`)}</p>
      {['step_ns', 'clearance_m', 'bypass_clearance_m'].map(key => key in (engine.config ?? {}) && <Leaf key={key} path={pointer('engines', name, 'config', key)} label={key} value={(engine.config as Draft)[key]} onChange={edit} focus={focus} />)}
      <TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} depth={0} />
    </details>)}
    {assessment.map(([name]) => <p key={name} className="hint">{L(`Traffic assessment engine "${name}" bids nearby UAVs for the incident task; it does not create vehicles.`, `交通评估引擎“${name}”让附近无人机竞标事件任务；它不创建车辆。`)}</p>)}
  </div>;
}

/** "street-east" passes through; "20,40,0" becomes an ENU position — never a silent default. */
const parseAt = (text: string): string | number[] => {
  const parts = text.split(',').map(part => Number(part.trim()));
  if (parts.length === 3 && parts.every(value => Number.isFinite(value))) return parts;
  return text.trim();
};

/** Incidents: the tasks cooperation owns this structure, so every change goes through the C11
 *  operations add_incident / set_incident / remove_incident with a dry-run review before applying —
 *  the form never hand-writes cooperation.incidents (design rule 5). */
export function IncidentsBlock({ config, api, project, onProject, saveDraft, focus, L }: {
  config: Draft;
  /** C11 wiring; when a caller omits it the block stays read-only and says so — never a silent hand-edit. */
  api?: SectionProps['api']; project?: SectionProps['project']; onProject?: SectionProps['onProject']; saveDraft?: SectionProps['saveDraft'];
  focus?: string; L: SectionProps['L'];
}) {
  const incidents = Array.isArray((config.cooperation as Draft | undefined)?.incidents)
    ? (config.cooperation as Draft).incidents as Draft[] : [];
  const readonly = !(api && project && onProject && saveDraft);
  const [editing, setEditing] = useState<string>();
  const [editId, setEditId] = useState(''), [editAt, setEditAt] = useState(''), [editTime, setEditTime] = useState('');
  const editTimeProblem = editing !== undefined ? stagedNumberProblem(editTime, { min: 0, zh: false }) : undefined;
  const editReady = editId.trim() !== '' && editAt.trim() !== '' && editTime !== '' && !editTimeProblem;
  const [newId, setNewId] = useState(''), [newAt, setNewAt] = useState(''), [newTime, setNewTime] = useState('90');
  const newTimeProblem = stagedNumberProblem(newTime, { min: 0, zh: false });
  const newReady = newId.trim() !== '' && newAt.trim() !== '' && !newTimeProblem;
  return <div>
    {incidents.length === 0 && <p className="hint">{L('No incidents configured; schedule one below, or under Cooperation.', '未配置事件；可在下方或“协同”中安排。')}</p>}
    {readonly && incidents.length > 0 && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L(
      'Incident changes need the operations client, which this view did not pass; open the section from the workbench to edit them.',
      '事件修改需要操作客户端，此视图未提供；请从工作台打开该区块进行编辑。')}</p>}
    {incidents.map(incident => {
      const id = String(incident.id ?? '');
      return <div key={id || String(incident.at_s)} className="wb-gust-row" data-testid="wb-incident">
        <span className="wb-mono">{id || '—'}</span>
        <span className="wb-tag">{String(incident.kind ?? '?')}</span>
        <span className="wb-tag">{incident.at_s !== undefined ? `${incident.at_s} s` : L('no time', '无时间')}</span>
        {!readonly && <button className="wb-btn small" aria-expanded={editing === id} onClick={() => {
          if (editing === id) { setEditing(undefined); return; }
          setEditing(id); setEditId(id);
          setEditAt(typeof incident.at === 'string' ? incident.at : Array.isArray(incident.at) ? incident.at.join(',') : '');
          setEditTime(incident.at_s !== undefined ? String(incident.at_s) : '');
        }}>{editing === id ? L('Close', '收起') : L('Change', '修改')}</button>}
        {!readonly && <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
          operations={[operation('remove_incident', { id })]}
          label={L('Remove', '移除')} labelZh="移除" danger small />}
      </div>;
    })}
    {editing !== undefined && <div style={{ border: '1px solid var(--wb-line)', borderRadius: 10, padding: 10, margin: '6px 0' }} data-testid="wb-incident-edit">
      <p className="hint" style={{ margin: '0 0 6px' }}>{L(
        `Change incident ${editing}: the dry run shows the exact field diff before anything is written.`,
        `修改事件 ${editing}：预演会先显示精确的字段差异，之后才写入。`)}</p>
      <div style={{ display: 'grid', gap: 6 }}>
        <label>{L('At (place id or east,north,up metres)', '位置（地点标识或东西,南北,垂直 米）')}
          <input className="wb-search" style={{ height: 28 }} aria-label={L('Incident position', '事件位置')}
            placeholder="street-east 或 20,40,0" value={editAt} onChange={event => setEditAt(event.target.value)} /></label>
        <StagedNumber label={`${L('Time', '时间')} (s)`} value={editTime} min={0} onChange={setEditTime} />
      </div>
      {!editReady && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{editTimeProblem ?? L('Give a position and a time to enable the change.', '请填写位置与时间后才能修改。')}</p>}
      {api && project && onProject && saveDraft && <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
        operations={editReady ? [operation('set_incident', { id: editId, at: parseAt(editAt), time_s: Number(editTime) })] : undefined}
        missing={!editReady ? editTimeProblem ?? L('Give a position and a time to enable the change.', '请填写位置与时间后才能修改。') : undefined}
        label={L('Preview incident change', '预览事件修改')} labelZh="预览事件修改" primary small
        onApplied={() => setEditing(undefined)} />}
    </div>}
    <div style={{ display: 'grid', gap: 6, marginTop: 6 }}>
      <input className="wb-search" style={{ height: 28 }} aria-label={L('Incident ID', '事件标识')} placeholder="fall-east" value={newId} onChange={event => setNewId(event.target.value)} />
      <label>{L('At (place id or east,north,up metres)', '位置（地点标识或东西,南北,垂直 米）')}
        <input className="wb-search" style={{ height: 28 }} aria-label={L('Incident position', '事件位置')} placeholder="street-east 或 20,40,0" value={newAt} onChange={event => setNewAt(event.target.value)} /></label>
      <StagedNumber label={`${L('Time', '时间')} (s)`} value={newTime} min={0} onChange={setNewTime} />
    </div>
    {!newReady && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{newTimeProblem ?? L('Give the incident an ID, a position and a time first.', '请先填写事件标识、位置与时间。')}</p>}
    {newReady && api && project && onProject && saveDraft && <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
      operations={[operation('add_incident', { id: newId.trim(), kind: 'person_fall', at: parseAt(newAt), time_s: Number(newTime) })]}
      label={L('Schedule incident', '安排事件')} labelZh="安排事件" primary small
      onApplied={() => { setNewId(''); setNewAt(''); }} />}
  </div>;
}
