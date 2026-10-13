import { useState } from 'react';
import { operation } from '../operations';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { ID_PATTERN, nextRoleId, num, strictVector3 } from './model';
import { OperationDiff, operationChanges, operationText, useOperationsReview } from './Operations';

const FLEET_ROLES = [
  { role: 'meal_courier', en: 'Meal courier', zh: '送餐无人机' },
  { role: 'parcel_drone', en: 'Parcel drone', zh: '包裹无人机' },
  { role: 'medical_drone', en: 'Medical drone', zh: '医疗无人机' },
  { role: 'inspection_drone', en: 'Inspection drone', zh: '巡检无人机' },
  { role: 'patrol_drone', en: 'Patrol drone', zh: '巡逻无人机' },
  { role: 'air_taxi', en: 'Air taxi', zh: '空中出租车' },
  { role: 'sprayer', en: 'Sprayer', zh: '植保无人机' },
] as const;
type FleetRole = (typeof FLEET_ROLES)[number]['role'];
export type FleetEditing = { id: string; count: number; battery_pct?: number; start?: string | number[] };

/** Members a fleet owns, from the actual server-written `<id>-<n>` entities. */
export const fleetMembers = (entities: Array<Record<string, any>>, fleetId: string): string[] => {
  const pattern = new RegExp(`^${fleetId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}-(\\d+)$`);
  return entities.filter(entity => entity.type === 'lowalt:UAV').map(entity => String(entity.id)).filter(id => pattern.test(id))
    .sort((left, right) => Number(left.slice(fleetId.length + 1)) - Number(right.slice(fleetId.length + 1)));
};

/** Prefill only shared, real member state; differing or missing values stay per-member. */
export function fleetEditingOf(entities: Array<Record<string, any>>, id: string): FleetEditing {
  const ids = fleetMembers(entities, id), members = ids.map(member => entities.find(entity => entity.id === member)!);
  const first = members[0]?.facts;
  const position = first?.['lowalt.position'], soc = first?.['lowalt.soc'];
  return {
    id, count: members.length,
    ...(strictVector3(position) && members.every(member => JSON.stringify(member.facts?.['lowalt.position']) === JSON.stringify(position)) ? { start: position } : {}),
    ...(typeof soc === 'number' && Number.isFinite(soc) && members.every(member => member.facts?.['lowalt.soc'] === soc) ? { battery_pct: soc * 100 } : {}),
  };
}

/** Count-only edits send only count. Starts and batteries change only when explicitly edited. */
export function AddFleet({ api, project, draft, onProject, L, onClose, editing, initialRole = 'meal_courier', onAddPlace }: {
  api: WorkbenchApi; project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; onClose: () => void; editing?: FleetEditing; initialRole?: FleetRole; onAddPlace?: () => void;
}) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const facilities = entities.filter(entity => strictVector3(entity.facts?.['lowalt.site_position']));
  const members = editing ? fleetMembers(entities, editing.id) : [];
  const isUpdate = !!editing;
  const initialStart = editing?.start;
  const [role, setRole] = useState<FleetRole>(initialRole);
  const [idText, setIdText] = useState(() => editing?.id ?? nextRoleId(initialRole, entities.map(entity => entity.id)));
  const [count, setCount] = useState(isUpdate ? String(editing.count) : '3');
  const [startMode, setStartMode] = useState<'keep' | 'centre' | 'facility' | 'enu'>(
    strictVector3(initialStart) ? 'enu' : typeof initialStart === 'string' ? ['centre', 'center', 'middle'].includes(initialStart) ? 'centre' : 'facility' : isUpdate ? 'keep' : 'centre');
  const [facilityId, setFacilityId] = useState(typeof initialStart === 'string' ? initialStart : '');
  const [coordinates, setCoordinates] = useState(() => strictVector3(initialStart) ? initialStart.map(String) : ['0', '0', '0']);
  const [battery, setBattery] = useState(editing?.battery_pct !== undefined ? String(editing.battery_pct) : '');
  const [payload, setPayload] = useState('');
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const mark = (field: string) => setTouched(previous => new Set(previous).add(field));
  const idBad = !idText.trim() || !ID_PATTERN.test(idText.trim()) || (!isUpdate && entities.some(entity => entity.id === idText.trim() || String(entity.id).startsWith(`${idText.trim()}-`)));
  const countValue = num(count), batteryValue = num(battery), payloadValue = num(payload), vector = coordinates.map(num);
  const countInvalid = countValue === undefined || !Number.isInteger(countValue) || countValue < 1 || countValue > 1000;
  const batteryInvalid = battery.trim() !== '' && (batteryValue === undefined || batteryValue < 0 || batteryValue > 100)
    || isUpdate && touched.has('battery') && battery.trim() === '' && editing.battery_pct !== undefined;
  const payloadInvalid = payload.trim() !== '' && (payloadValue === undefined || payloadValue <= 0);
  const startInvalid = startMode === 'facility' ? !facilities.some(entity => entity.id === facilityId) : startMode === 'enu' && vector.some(value => value === undefined);
  const canPreview = !idBad && !countInvalid && !batteryInvalid && !payloadInvalid && !startInvalid;
  const start = startMode === 'centre' ? 'centre' : startMode === 'facility' ? facilityId : startMode === 'enu' ? vector : undefined;
  const resolveStart = (value: unknown) => value === 'centre' || value === 'center' || value === 'middle' ? [0, 0, 0]
    : typeof value === 'string' ? facilities.find(entity => entity.id === value)?.facts['lowalt.site_position'] ?? value : value;
  const params: Record<string, unknown> = isUpdate ? { fleet: editing.id } : { id: idText.trim(), role, count: countValue, start };
  if (isUpdate && countValue !== editing.count) params.count = countValue;
  if (isUpdate && start !== undefined && JSON.stringify(resolveStart(start)) !== JSON.stringify(resolveStart(initialStart))) params.start = start;
  if (battery.trim() !== '' && (!isUpdate || batteryValue !== editing.battery_pct)) params.battery_pct = batteryValue;
  if (!isUpdate && payload.trim() !== '') params.payload_capacity_kg = payloadValue;
  const review = useOperationsReview(api, project, [operation(isUpdate ? 'set_fleet' : 'add_fleet', params)]);
  const apply = async () => { if (!canPreview) return; const updated = await review.apply(); if (updated) { onProject(updated); onClose(); } };
  const removesMembers = !!review.preview && operationChanges(review.preview.diff, entities as Array<{ id: string }>).removed.length > 0;

  return <div className="wb-object-form wb-form-card" data-testid="wb-add-fleet">
    <h4 className="wb-form-card-title">{isUpdate ? L('Edit fleet', '编辑机群') : L('Add fleet', '添加机群')}</h4>
    {isUpdate && <p className="hint">{L(`Members: ${members.join(', ')}. Unchanged settings are preserved.`, `成员：${members.join('、')}。未修改的设置会保留。`)}</p>}
    {!isUpdate && <div className="wb-choice-group" role="group" aria-label={L('Fleet role', '机群角色')}>
      {FLEET_ROLES.map(row => <button type="button" key={row.role} aria-pressed={role === row.role} className="wb-choice"
        onClick={() => { setRole(row.role); if (!touched.has('id')) setIdText(nextRoleId(row.role, entities.map(entity => entity.id))); }}>{L(row.en, row.zh)}</button>)}
    </div>}
    <div className="wb-form-grid">
      <label className="wb-form-field" style={{ gridColumn: '1 / -1' }}><span className="wb-label">{L('fleet id', '机群 id')}</span><input className="wb-mono" value={idText} readOnly={isUpdate} aria-invalid={touched.has('id') && idBad}
        onChange={event => { setIdText(event.target.value); mark('id'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('Count', '数量')}</span><input inputMode="numeric" value={count} aria-invalid={touched.has('count') && countInvalid}
        onChange={event => { setCount(event.target.value); mark('count'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('Battery (%)', '电量 (%)')}</span><input inputMode="decimal" value={battery} aria-invalid={touched.has('battery') && batteryInvalid}
        placeholder={isUpdate ? L('Keep each member’s battery', '保留各成员电量') : L('Use the default battery', '使用默认电量')}
        onChange={event => { setBattery(event.target.value); mark('battery'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('Start place', '起点')}</span><select value={startMode} onChange={event => { setStartMode(event.target.value as typeof startMode); mark('start'); }}>
        {isUpdate && <option value="keep">{L('Keep member starts', '保留各成员起点')}</option>}
        <option value="centre">{L('Map centre', '地图中心')}</option>
        <option value="facility" disabled={!facilities.length}>{L('An existing place', '已有设施')}</option>
        <option value="enu">{L('Typed (east, north, up)', '手动输入（东、北、天）')}</option>
      </select></label>
      {startMode === 'facility' && <label className="wb-form-field" style={{ gridColumn: '1 / -1' }}><span className="wb-label">{L('start facility', '起点设施')}</span><select value={facilityId} disabled={!facilities.length} aria-invalid={touched.has('start') && startInvalid}
        onChange={event => { setFacilityId(event.target.value); mark('start'); }}>
        <option value="">{L('Choose…', '选择…')}</option>{facilities.map(entity => <option key={entity.id} value={entity.id}>{L(entity.label ?? entity.id, entity.label_zh ?? entity.label ?? entity.id)}</option>)}
      </select></label>}
      {startMode === 'enu' && <div className="wb-form-grid" style={{ gridColumn: '1 / -1' }}>
        {[L('east (m)', '东向 (m)'), L('north (m)', '北向 (m)'), L('up (m)', '天向 (m)')].map((label, index) => <label className="wb-form-field" key={index}>
          <span className="wb-label">{label}</span><input inputMode="decimal" value={coordinates[index]} aria-invalid={touched.has('start') && vector[index] === undefined}
            onChange={event => { setCoordinates(previous => previous.map((value, axis) => axis === index ? event.target.value : value)); mark('start'); }} />
        </label>)}
      </div>}
      {!isUpdate && <label className="wb-form-field"><span className="wb-label">{L('payload capacity (kg)', '载荷容量 (kg)')}</span><input inputMode="decimal" value={payload} aria-invalid={touched.has('payload') && payloadInvalid}
        placeholder={L('Use the default capacity', '使用默认容量')} onChange={event => { setPayload(event.target.value); mark('payload'); }} /></label>}
    </div>
    {!facilities.length && <p className="wb-form-prerequisite">{L('No depot or customer places yet — add a place to use as the fleet start.', '还没有仓库或客户设施——请添加一个作为机群起点。')}
      {onAddPlace && <button type="button" className="wb-btn small" onClick={onAddPlace}>{L('Add place', '添加设施')}</button>}</p>}
    {idBad && touched.has('id') && <p className="wb-field-error" role="alert">{L('Enter a unique fleet id without spaces.', '请输入唯一且不含空格的机群 id。')}</p>}
    {countInvalid && touched.has('count') && <p className="wb-field-error" role="alert">{L('Count must be a whole number from 1 to 1000.', '数量必须是 1 到 1000 之间的整数。')}</p>}
    {batteryInvalid && touched.has('battery') && <p className="wb-field-error" role="alert">{L('Battery must be a number between 0 and 100.', '电量必须是 0 到 100 之间的数字。')}</p>}
    {payloadInvalid && touched.has('payload') && <p className="wb-field-error" role="alert">{L('Payload capacity must be a positive number.', '载荷容量必须是正数。')}</p>}
    {startInvalid && touched.has('start') && <p className="wb-field-error" role="alert">{L('Choose a facility or enter three finite coordinates.', '请选择设施或输入三个有限坐标。')}</p>}
    {review.preview && <OperationDiff {...review.preview} entities={entities as Array<{ id: string }>} stale={review.stale} L={L} />}
    {review.problem && <p className="wb-field-error" role="alert">{review.problem.field && <span className="wb-mono">{review.problem.field}: </span>}{operationText(review.problem.message, L)}</p>}
    <div className="wb-form-actions wb-object-actions">
      <button className="wb-btn" disabled={!canPreview || !!review.busy} onClick={() => void review.load()}>{review.busy === 'preview' ? L('Previewing…', '预览中…') : review.preview ? L('Preview again', '重新预览') : L('Preview', '预览')}</button>
      {review.preview && <button className="wb-btn primary" disabled={!canPreview || review.stale || !!review.busy} onClick={() => void apply()}>
        {review.busy === 'apply' ? L('Applying…', '应用中…') : removesMembers ? L('Confirm resize', '确认调整规模') : L('Apply', '应用')}</button>}
      <button className="wb-btn" disabled={!!review.busy} onClick={onClose}>{L('Close', '关闭')}</button>
    </div>
  </div>;
}
