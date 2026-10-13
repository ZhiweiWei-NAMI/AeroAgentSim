import { useMemo, useState } from 'react';
import { applyOperations, operation, operationProblem, previewOperations, type OperationPreview } from '../operations';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { ID_PATTERN, num } from './model';

/** Fleet roles come from the server vocabulary (GET /v1/studio/operations); no client invention. */
const FLEET_ROLES = [
  { role: 'meal_courier', en: 'Meal courier', zh: '送餐无人机' },
  { role: 'parcel_drone', en: 'Parcel drone', zh: '包裹无人机' },
  { role: 'medical_drone', en: 'Medical drone', zh: '医疗无人机' },
  { role: 'inspection_drone', en: 'Inspection drone', zh: '巡检无人机' },
  { role: 'patrol_drone', en: 'Patrol drone', zh: '巡逻无人机' },
  { role: 'air_taxi', en: 'Air taxi', zh: '空中出租车' },
  { role: 'sprayer', en: 'Sprayer', zh: '植保无人机' },
] as const;

/** Members a fleet owns, from its server-written entities: `<id>-<n>` with the same vehicle class label set. */
export const fleetMembers = (entities: Array<Record<string, any>>, fleetId: string): string[] => {
  const pattern = new RegExp(`^${fleetId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}-(\\d+)$`);
  return entities.map(entity => String(entity.id)).filter(id => pattern.test(id))
    .sort((left, right) => Number(left.slice(fleetId.length + 1)) - Number(right.slice(fleetId.length + 1)));
};

/** Add or edit an explicit UAV fleet through C11 add_fleet / set_fleet. */
export function AddFleet({ api, project, draft, onProject, L, onClose, editing }: {
  api: WorkbenchApi; project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; onClose: () => void;
  editing?: { id: string; count: number; battery_pct?: number; start?: string | number[] };
}) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const facilities = entities.filter(entity => entity.facts && ('lowalt.site_position' in entity.facts || 'lowalt.position' in entity.facts));
  const members = editing ? fleetMembers(entities, editing.id) : [];
  const isUpdate = !!editing;
  const [role, setRole] = useState<(typeof FLEET_ROLES)[number]['role'] | ''>('');
  const [idText, setIdText] = useState(editing?.id ?? '');
  const [count, setCount] = useState(isUpdate ? String(editing!.count) : '3');
  const [startMode, setStartMode] = useState<'centre' | 'facility'>(typeof editing?.start === 'string' ? 'facility' : 'centre');
  const [facilityId, setFacilityId] = useState(typeof editing?.start === 'string' ? editing.start : '');
  const [battery, setBattery] = useState(editing?.battery_pct !== undefined ? String(editing.battery_pct) : '');
  const [payload, setPayload] = useState('');
  const [preview, setPreview] = useState<OperationPreview>();
  const [previewStale, setPreviewStale] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'apply' | undefined>();
  const [problem, setProblem] = useState<{ field?: string; message: string }>();

  const chosen = FLEET_ROLES.find(row => row.role === role);
  const shrink = isUpdate && num(count) !== undefined && Number(count) < members.length;
  const toRemove = shrink ? members.slice(Number(count)) : [];
  const idBad = !isUpdate && (!idText.trim() || !ID_PATTERN.test(idText.trim()) ||
    entities.some(entity => entity.id === idText.trim()) ||
    entities.some(entity => String(entity.id).startsWith(`${idText.trim()}-`)));
  const countInvalid = num(count) === undefined || !Number.isInteger(Number(count)) || Number(count) < 1;
  const batteryInvalid = battery.trim() !== '' && (num(battery) === undefined || Number(battery) < 0 || Number(battery) > 100);
  const payloadInvalid = payload.trim() !== '' && (num(payload) === undefined || Number(payload) <= 0);
  const roleMissing = !isUpdate && !chosen;
  const startInvalid = startMode === 'facility' && !facilityId;
  const canPreview = !roleMissing && !idBad && !countInvalid && !batteryInvalid && !payloadInvalid && !startInvalid;

  const params = () => {
    const start = startMode === 'facility' ? facilityId : 'centre';
    if (isUpdate) {
      const body: Record<string, unknown> = { fleet: editing!.id, count: Number(count) };
      if (battery.trim() !== '') body.battery_pct = Number(battery);
      body.start = start;
      return body;
    }
    const body: Record<string, unknown> = { id: idText.trim(), role, count: Number(count), start };
    if (battery.trim() !== '') body.battery_pct = Number(battery);
    if (payload.trim() !== '') body.payload_capacity_kg = Number(payload);
    return body;
  };

  const touch = () => { setPreviewStale(!!preview); };
  const load = async () => {
    if (!canPreview) return;
    setBusy('preview'); setProblem(undefined);
    try {
      const answer = await previewOperations(api, project, [operation(isUpdate ? 'set_fleet' : 'add_fleet', params())]);
      setPreview(answer); setPreviewStale(false);
    } catch (failure) { setProblem(operationProblem(failure)); setPreview(undefined); }
    finally { setBusy(undefined); }
  };
  const apply = async () => {
    if (!preview || previewStale) return;
    setBusy('apply'); setProblem(undefined);
    try {
      const updated = await applyOperations(api, project, [operation(isUpdate ? 'set_fleet' : 'add_fleet', params())]);
      onProject(updated); onClose();
    } catch (failure) { setProblem(operationProblem(failure)); setPreviewStale(true); }
    finally { setBusy(undefined); }
  };

  return <div style={{ border: '1px solid var(--wb-line)', borderRadius: 12, padding: '10px 12px', marginBottom: 12 }} data-testid="wb-add-fleet">
    <h4 style={{ margin: '2px 0 4px' }}>{isUpdate ? L('Edit fleet', '编辑机群') : L('Add fleet', '添加机群')}</h4>
    {isUpdate && <p className="hint">{L(
      `Server summary id "${editing!.id}" owns members ${members.join(', ') || '(none)'}; shrinking removes exactly the listed members below.`,
      `服务器摘要 id "${editing!.id}" 拥有成员 ${members.join('、') || '（无）'}；缩小规模只会移除下方列出的成员。`)}</p>}
    {!isUpdate && <>
      <label className="hint" style={{ display: 'block', margin: '4px 0 2px' }}>{L('Role', '角色')}</label>
      <div role="radiogroup" aria-label={L('Fleet role', '机群角色')} style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {FLEET_ROLES.map(row => <button key={row.role} role="radio" aria-checked={role === row.role} className="wb-chip" style={{ cursor: 'pointer' }}
          onClick={() => { setRole(row.role); touch(); }}>{L(row.en, row.zh)}</button>)}
      </div>
      {roleMissing && <p className="wb-add-note" role="status">{L('Choose a role to continue.', '请先选择角色。')}</p>}
    </>}
    <div className="wb-add-grid">
      <label className="wb-field"><span>{L('fleet id', '机群 id')}</span>
        <input className="wb-mono" value={idText} readOnly={isUpdate} aria-invalid={idBad} placeholder="couriers"
          onChange={event => { setIdText(event.target.value); touch(); }} /></label>
      <label className="wb-field"><span>{L('count (drones)', '数量（架）')}</span>
        <input inputMode="numeric" value={count} aria-invalid={countInvalid}
          onChange={event => { setCount(event.target.value); touch(); }} /></label>
      <label className="wb-field"><span>{L('start', '起点')}</span>
        <select value={startMode} onChange={event => { setStartMode(event.target.value as 'centre' | 'facility'); touch(); }}>
          <option value="centre">{L('Map centre (ENU origin)', '地图中心（ENU 原点）')}</option>
          <option value="facility">{L('An existing facility', '已有设施')}</option>
        </select></label>
      {startMode === 'facility' && <label className="wb-field"><span>{L('start facility', '起点设施')}</span>
        <select value={facilityId} aria-invalid={startInvalid} onChange={event => { setFacilityId(event.target.value); touch(); }}>
          <option value="">{L('Choose…', '选择…')}</option>
          {facilities.map(entity => <option key={entity.id} value={entity.id}>{entity.id}</option>)}
        </select></label>}
      <label className="wb-field"><span>{L('initial battery (%)', '初始电量 (%)')}</span>
        <input inputMode="decimal" value={battery} aria-invalid={batteryInvalid} placeholder={L('server default', '服务器默认值')}
          onChange={event => { setBattery(event.target.value); touch(); }} /></label>
      {!isUpdate && <label className="wb-field"><span>{L('payload capacity (kg)', '载荷容量 (kg)')}</span>
        <input inputMode="decimal" value={payload} aria-invalid={payloadInvalid} placeholder={L('server default', '服务器默认值')}
          onChange={event => { setPayload(event.target.value); touch(); }} /></label>}
    </div>
    {(idBad || countInvalid || batteryInvalid || payloadInvalid || startInvalid) && <p className="wb-field-error" role="alert">
      {idBad ? L('Use letters, digits and . _ : / - with no spaces, and an id (or id prefix) no instance already uses.', '只能使用字母、数字和 . _ : / -，且 id（或前缀）不能与已有实例重复。')
        : countInvalid ? L('Count must be a whole number ≥ 1.', '数量必须是不小于 1 的整数。')
        : batteryInvalid ? L('Battery must be a number between 0 and 100.', '电量必须是 0 到 100 之间的数字。')
        : payloadInvalid ? L('Payload capacity must be a positive number.', '载荷容量必须是正数。')
        : L('Choose the start facility.', '请选择起点设施。')}</p>}
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '8px 0 2px' }}>
      <button className="wb-btn" disabled={!canPreview || busy !== undefined} onClick={() => void load()}>
        {busy === 'preview' ? L('Previewing…', '预览中…') : preview ? L('Re-preview', '重新预览') : L('Preview dry-run diff', '预览预演差异')}</button>
      {preview && <button className="wb-btn primary" disabled={previewStale || busy !== undefined} onClick={() => void apply()}>
        {busy === 'apply' ? L('Applying…', '应用中…') : L('Apply', '应用')}</button>}
      <button className="wb-btn ghost" disabled={busy !== undefined} onClick={onClose}>{L('Close', '关闭')}</button>
      {!canPreview && !roleMissing && <span className="wb-add-note">{L('Fix the marked fields to enable the preview.', '修正标记的字段后才能预览。')}</span>}
    </div>
    {preview && <div role="status" style={{ margin: '6px 0 2px' }}>
      <b>{L('Server dry-run diff', '服务器预演差异')}{previewStale ? ` — ${L('out of date; preview again', '已过期；请重新预览')}` : ''}</b>
      <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
        {preview.diff.added.map((row, index) => <li key={`a${index}`}><span className="wb-tag ok">+</span> <span className="wb-mono">{row.kind ?? ''}</span> {row.label ?? row.id} <span className="wb-mono" style={{ fontSize: 10, color: 'var(--wb-muted)' }}>{row.id}</span></li>)}
        {preview.diff.changed.map((row, index) => <li key={`c${index}`}>
          <span className="wb-tag warn">~</span> <span className="wb-mono">{row.kind ?? ''}</span> {row.label ?? row.id}
          {(row.fields ?? []).map(field => <div key={field.name} className="wb-mono" style={{ fontSize: 11, paddingLeft: 14 }}>
            {field.name}: {JSON.stringify(field.before)} → {JSON.stringify(field.after)}</div>)}
        </li>)}
        {preview.diff.removed.map((row, index) => <li key={`r${index}`}><span className="wb-tag warn">−</span> <span className="wb-mono">{row.kind ?? ''}</span> {row.label ?? row.id}</li>)}
        {!preview.diff.added.length && !preview.diff.changed.length && !preview.diff.removed.length &&
          <li>{L('The server reports no draft changes for these exact inputs.', '服务器报告：对当前输入没有草稿变更。')}</li>}
      </ul>
      {shrink && <p className="wb-add-note" style={{ color: 'var(--wb-warn)' }}>{L(
        `Shrinking this fleet removes: ${toRemove.join(', ')}. The diff above is the authoritative list.`,
        `缩小该机群将移除：${toRemove.join('、')}。上方差异为权威清单。`)}</p>}
      {preview.notes.map((note, index) => <p key={index} className="hint" style={{ margin: '2px 0' }}>
        {typeof note === 'string' ? note : note.en ?? note.zh ?? note.text}</p>)}
    </div>}
    {problem && <p role="alert" style={{ color: 'var(--wb-bad)', margin: '6px 0 0' }}>
      {problem.field ? <><span className="wb-mono">{problem.field}</span>: </> : null}{problem.message}</p>}
  </div>;
}
