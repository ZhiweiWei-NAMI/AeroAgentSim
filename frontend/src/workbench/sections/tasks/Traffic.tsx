import { useMemo, useState } from 'react';
import { useI18n } from '../../../i18n/I18nProvider';
import type { SectionProps } from '../types';
import { operation } from '../operations';
import { useStagedForm } from '../useStagedForm';
import type { Draft } from './model';
import { OpApply, StagedFormNote, StagedNumber, parseStagedNumber, stagedNumberProblem } from './op-ui';
import { pointer, TreeForm } from '../../form';

/** Traffic sources retain their actual provenance. */
export function TrafficBlock({ draft, edit, focus, L }: { draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'] }) {
  const engines = (draft.engines ?? {}) as Record<string, Draft>;
  const sumo = Object.entries(engines).filter(([, engine]) => engine?.plugin === 'sumo');
  const procedural = Object.entries(engines).filter(([, engine]) => engine?.plugin === 'traffic_road_motion');
  const assessment = Object.entries(engines).filter(([, engine]) => engine?.plugin === 'traffic_assessment');
  return <div className="wb-traffic" data-testid="wb-traffic">
    {!sumo.length && !procedural.length && <div className="wb-empty">{L('No road traffic engine is configured.', '尚未配置道路交通引擎。')}</div>}
    {sumo.map(([name, engine]) => <details key={name}><summary>SUMO <span className="wb-tag wb-mono">{name}</span></summary>
      <p className="hint">{L('Vehicle counts, arrivals and routes use the SUMO network configuration.', '车辆数量、到达时间与路线由 SUMO 路网配置决定。')}</p>
      <details><summary>{L('Advanced', '高级')}</summary><TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} depth={1} /></details>
    </details>)}
    {procedural.map(([name, engine]) => <details key={name}><summary>{L('Scripted road traffic', '脚本道路交通')} <span className="wb-tag wb-mono">{name}</span></summary>
      <p className="hint">{L('Vehicles follow the configured routes and speeds. This engine uses scripted traffic.', '车辆沿配置的路线按配置速度行驶，此引擎使用脚本交通。')}</p>
      <details><summary>{L('Advanced', '高级')}</summary><TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} /></details>
    </details>)}
    {assessment.map(([name]) => <p key={name} className="hint">{L(`Traffic assessment "${name}" assigns nearby UAVs to incident tasks.`, `交通评估“${name}”为事件任务指派附近的无人机。`)}</p>)}
  </div>;
}

/** Coordinate-like input must contain three actual numbers; missing coordinates never become zero. */
export function parseIncidentPosition(text: string): string | number[] | undefined {
  if (!text.trim()) return undefined;
  if (!text.includes(',')) return text.trim();
  const parts = text.split(',').map(parseStagedNumber);
  return parts.length === 3 && parts.every((value): value is number => value !== undefined) ? parts as number[] : undefined;
}

type IncidentFields = { id: string; at: string; time: string };
type IncidentWiring = Pick<SectionProps, 'api' | 'project' | 'onProject' | 'saveDraft'>;
type IncidentProps = {
  config: Draft; duration_s?: number; focus?: string; L: SectionProps['L'];
  api?: SectionProps['api']; project?: SectionProps['project']; onProject?: SectionProps['onProject']; saveDraft?: SectionProps['saveDraft'];
};

function IncidentPosition({ value, onChange, touched, L }: { value: string; onChange: (value: string) => void; touched: boolean; L: SectionProps['L'] }) {
  return <label className="wb-form-field"><span className="wb-label">{L('Place ID or east, north, up (m)', '设施标识或东、北、上坐标（米）')}</span>
    <input aria-label={L('Incident position', '事件位置')} placeholder={L('e.g. street-east or 20,40,0', '例如 street-east 或 20,40,0')} value={value} onChange={event => onChange(event.target.value)} />
    {touched && parseIncidentPosition(value) === undefined && <span role="alert" className="hint wb-field-error">{L('Enter a place ID or three finite coordinates.', '请输入设施标识或三个有限坐标。')}</span>}
  </label>;
}

function IncidentEditForm({ incident, wiring, duration_s, L, zh, onClose }: { incident: Draft; wiring: IncidentWiring; duration_s?: number; L: SectionProps['L']; zh: boolean; onClose: () => void }) {
  const at = incident.at ?? incident.position;
  const form = useStagedForm<IncidentFields>(wiring.project.id, `incident.edit:${incident.id}`, {
    id: incident.id, at: typeof at === 'string' ? at : Array.isArray(at) ? at.join(',') : '',
    time: incident.at_s === undefined ? '' : String(incident.at_s),
  }, wiring.project.revision);
  const fields = form.values;
  const [touched, setTouched] = useState(false);
  const valid = fields.id === incident.id && parseIncidentPosition(fields.at) !== undefined && !stagedNumberProblem(fields.time, { min: 0, max: duration_s, zh });
  const operations = useMemo(() => valid ? [operation('set_incident', { id: incident.id, at: parseIncidentPosition(fields.at), time_s: parseStagedNumber(fields.time) })] : undefined, [valid, incident.id, fields.at, fields.time]);
  return <div className="wb-form-card wb-form-stack wb-incident-edit" data-testid="wb-incident-edit">
    <p className="hint">{L(`Change incident "${incident.id}". Review the changes before applying.`, `修改事件“${incident.id}”，应用前请核对变更。`)}</p>
    <StagedFormNote form={form} L={L} onDiscard={() => setTouched(false)} />
    <IncidentPosition L={L} value={fields.at} touched={touched} onChange={at => { setTouched(true); form.setValues(previous => ({ ...previous, at })); }} />
    <StagedNumber label={`${L('Time', '时间')} (s)`} value={fields.time} min={0} max={duration_s} onChange={time => form.setValues(previous => ({ ...previous, time }))} />
    <OpApply {...wiring} operations={operations} label={L('Preview', '预览')} primary small onApplied={() => { form.clear(form.values); onClose(); }} />
  </div>;
}

/** Incidents use the persisted server IDs and positions; each editor keeps its own staged buffer. */
export function IncidentsBlock({ config, api, project, onProject, saveDraft, duration_s, L }: IncidentProps) {
  const { locale } = useI18n(), zh = locale === 'zh-CN';
  const incidents = Array.isArray(config.cooperation?.incidents) ? config.cooperation.incidents as Draft[] : [];
  const [editingId, setEditingId] = useState<string>();
  const form = useStagedForm<IncidentFields>(project?.id, 'incident.add', {
    id: '', at: '', time: duration_s !== undefined && duration_s > 0 ? String(duration_s * 0.25) : '',
  }, project?.revision);
  const fields = form.values;
  const [touched, setTouched] = useState<Set<string>>(() => new Set());
  const valid = !!fields.id.trim() && parseIncidentPosition(fields.at) !== undefined && !stagedNumberProblem(fields.time, { min: 0, max: duration_s, zh });
  const newOperations = useMemo(() => valid ? [operation('add_incident', {
    id: fields.id.trim(), kind: 'person_fall', at: parseIncidentPosition(fields.at), time_s: parseStagedNumber(fields.time),
  })] : undefined, [valid, fields.id, fields.at, fields.time]);
  const removals = useMemo(() => incidents.map(incident => typeof incident.id === 'string' && incident.id.trim()
    ? [operation('remove_incident', { id: incident.id })] : undefined), [JSON.stringify(incidents.map(incident => incident.id))]);
  const update = (key: keyof IncidentFields, value: string) => {
    setTouched(previous => new Set([...previous, key]));
    form.setValues(previous => ({ ...previous, [key]: value }));
  };
  const wiring = api && project && onProject && saveDraft ? { api, project, onProject, saveDraft } : undefined;
  return <div className="wb-form-section">
    {!incidents.length && <p className="hint">{L('No incidents configured. Schedule one below.', '尚未配置事件，可在下方安排。')}</p>}
    {!wiring && <p className="hint wb-form-prerequisite">{L('Open this form from the workbench to change incidents.', '请从工作台打开此表单以修改事件。')}</p>}
    {incidents.map((incident, index) => {
      const id = typeof incident.id === 'string' && incident.id.trim() ? incident.id : undefined;
      return <div key={id ?? index}>
        <div className="wb-gust-row" data-testid="wb-incident">
          <span className="wb-mono">{id ?? L('Missing ID', '缺少标识')}</span>
          <span className="wb-tag">{incident.kind === 'person_fall' ? L('Person fell', '行人跌倒') : L('Unsupported incident kind', '不支持的事件类型')}</span>
          <span className="wb-tag">{incident.at_s !== undefined ? `${incident.at_s} s` : L('No time set', '尚未设置时间')}</span>
          {wiring && <>
            <button type="button" className="wb-btn small" disabled={!id} aria-expanded={editingId === id} onClick={() => { if (id) setEditingId(previous => previous === id ? undefined : id); }}>{editingId === id && id ? L('Close', '收起') : L('Change', '修改')}</button>
            <OpApply {...wiring} operations={removals[index]} label={L('Preview removal', '预览移除')} danger small />
          </>}
        </div>
        {editingId === id && id && wiring && <IncidentEditForm key={id} incident={incident} wiring={wiring} duration_s={duration_s} L={L} zh={zh} onClose={() => setEditingId(undefined)} />}
      </div>;
    })}
    {wiring && <fieldset className="wb-form-card wb-form-stack">
      <legend className="wb-form-card-title">{L('Add incident', '添加事件')}</legend>
      <StagedFormNote form={form} L={L} onDiscard={() => setTouched(new Set())} />
      <label className="wb-form-field"><span className="wb-label">{L('Incident ID', '事件标识')}</span>
        <input aria-label={L('Incident ID', '事件标识')} placeholder={L('e.g. fall-east', '例如 fall-east')} value={fields.id} onChange={event => update('id', event.target.value)} />
        {touched.has('id') && !fields.id.trim() && <span role="alert" className="hint wb-field-error">{L('Required.', '必填。')}</span>}
      </label>
      <IncidentPosition L={L} value={fields.at} touched={touched.has('at')} onChange={value => update('at', value)} />
      <StagedNumber label={`${L('Time', '时间')} (s)`} value={fields.time} min={0} max={duration_s} onChange={value => update('time', value)} />
      <OpApply {...wiring} operations={newOperations} label={L('Preview', '预览')} primary small onApplied={() => { form.clear(form.values); setTouched(new Set()); }} />
    </fieldset>}
  </div>;
}
