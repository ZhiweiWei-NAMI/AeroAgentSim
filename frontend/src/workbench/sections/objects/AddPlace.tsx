import { useMemo, useState } from 'react';
import { operation } from '../operations';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { ID_PATTERN, nextRoleId, num, PLACE_ROLES, placeRoleOf, strictVector3, type PlaceEditing, type PlaceRole } from './model';
import { OperationDiff, operationText, useOperationsReview } from './Operations';
import { RegionMap, type RegionMapGeometry } from '../space/RegionMap';
import { readRegion } from '../space/model';

type PositionMode = 'centre' | 'facility' | 'enu';

/** All lowalt place mutations use C11; an edit retains its role and current service settings. */
export function AddPlace({ api, project, draft, project_detail, onProject, L, onClose, editing, initialRole = 'charging_pad' }: {
  api: WorkbenchApi; project: Project; draft: Scenario; project_detail?: { region?: unknown; scene?: unknown };
  onProject: (project: Project) => void; L: (en: string, zh: string) => string; onClose: () => void;
  editing?: PlaceEditing; initialRole?: PlaceRole;
}) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const facilities = entities.filter(entity => strictVector3(entity.facts?.['lowalt.site_position']));
  const isUpdate = !!editing;
  const savedRole = editing?.role ?? placeRoleOf(editing?.type);
  const [role, setRole] = useState<PlaceRole | undefined>(isUpdate ? savedRole : initialRole);
  const [idText, setIdText] = useState(() => editing?.id ?? nextRoleId(initialRole, entities.map(entity => entity.id)));
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const mark = (field: string) => setTouched(previous => new Set(previous).add(field));
  const [labelEn, setLabelEn] = useState(editing?.label ?? '');
  const [labelZh, setLabelZh] = useState(editing?.label_zh ?? '');
  const [mode, setMode] = useState<PositionMode>(isUpdate ? 'enu' : 'centre');
  const [facilityId, setFacilityId] = useState('');
  const savedPosition = strictVector3(editing?.position) ? editing.position : undefined;
  const [east, setEast] = useState(savedPosition ? String(savedPosition[0]) : isUpdate ? '' : '0');
  const [north, setNorth] = useState(savedPosition ? String(savedPosition[1]) : isUpdate ? '' : '0');
  const [up, setUp] = useState(savedPosition ? String(savedPosition[2]) : isUpdate ? '' : '0');
  // These are the documented C11 defaults, shown as editable values only for new places.
  const [capacity, setCapacity] = useState(editing?.capacity !== undefined ? String(editing.capacity) : isUpdate ? '' : '1');
  const [preparation, setPreparation] = useState(editing?.preparation_s !== undefined ? String(editing.preparation_s) : isUpdate ? '' : '30');
  const [power, setPower] = useState(editing?.power_w !== undefined ? String(editing.power_w) : isUpdate ? '' : '5000');
  const [picking, setPicking] = useState(false);
  const chosen = PLACE_ROLES.find(row => row.role === role);
  const hasCapacity = role !== 'handoff_point';
  const hasPreparation = role === 'kitchen' || (isUpdate && editing.preparation_s !== undefined);
  const hasPower = role === 'charging_pad' || (isUpdate && editing.power_w !== undefined);
  const idBad = !idText.trim() || !ID_PATTERN.test(idText.trim()) || (!isUpdate && entities.some(entity => entity.id === idText.trim()));
  const numbers = [num(east), num(north), num(up)];
  const positionInvalid = mode === 'enu' && numbers.some(value => value === undefined);
  const capacityValue = num(capacity), preparationValue = num(preparation), powerValue = num(power);
  const cleared = (field: string, raw: string, current: unknown) => isUpdate && touched.has(field) && raw.trim() === '' && current !== undefined;
  const capacityInvalid = hasCapacity && (capacity.trim() !== '' && (capacityValue === undefined || !Number.isInteger(capacityValue) || capacityValue < 1 || capacityValue > 1000) || cleared('capacity', capacity, editing?.capacity));
  const preparationInvalid = hasPreparation && (preparation.trim() !== '' && (preparationValue === undefined || preparationValue <= 0) || cleared('preparation', preparation, editing?.preparation_s));
  const powerInvalid = hasPower && (power.trim() !== '' && (powerValue === undefined || powerValue <= 0) || cleared('power', power, editing?.power_w));
  const labelInvalid = cleared('label', labelEn, editing?.label) || cleared('label_zh', labelZh, editing?.label_zh);
  const at = mode === 'centre' ? 'centre' : mode === 'facility' ? facilityId : numbers;
  const startInvalid = mode === 'facility' && !facilities.some(entity => entity.id === facilityId);
  const canPreview = (isUpdate || !!chosen) && !idBad && !positionInvalid && !startInvalid && !capacityInvalid && !preparationInvalid && !powerInvalid && !labelInvalid;
  const params: Record<string, unknown> = { id: idText.trim() };
  if (!isUpdate) params.role = role;
  if (!isUpdate || JSON.stringify(at) !== JSON.stringify(editing.position)) params.at = at;
  if (labelEn.trim() !== (editing?.label ?? '') && labelEn.trim() !== '') params.label = labelEn.trim();
  if (labelZh.trim() !== (editing?.label_zh ?? '') && labelZh.trim() !== '') params.label_zh = labelZh.trim();
  if (hasCapacity && capacity.trim() !== '' && (!isUpdate || capacityValue !== editing.capacity)) params.capacity = capacityValue;
  if (hasPreparation && preparation.trim() !== '' && (!isUpdate || preparationValue !== editing.preparation_s)) params.preparation_s = preparationValue;
  if (hasPower && power.trim() !== '' && (!isUpdate || powerValue !== editing.power_w)) params.power_w = powerValue;
  const review = useOperationsReview(api, project, [operation(isUpdate ? 'update_place' : 'add_place', params)]);
  const apply = async () => { if (!canPreview) return; const updated = await review.apply(); if (updated) { onProject(updated); onClose(); } };

  const region = useMemo(() => readRegion(project_detail as never), [project_detail]);
  const mapOrigin = region?.scene?.origin ?? null;
  const mapGeometry: RegionMapGeometry | null = mapOrigin ? { origin: mapOrigin } : null;
  const canPick = !!mapOrigin && !!region?.region;
  const marker: [number, number] | null = mode === 'enu' && numbers[0] !== undefined && numbers[1] !== undefined ? [numbers[0], numbers[1]] : null;

  return <div className="wb-object-form" data-testid="wb-add-place">
    <h4>{isUpdate ? L('Edit place', '编辑设施') : L('Add place', '添加设施')}</h4>
    <p className="hint">{L('Set the place and its service settings, then review the changes before applying.', '设置设施及服务参数，查看变更后再应用。')}</p>
    {isUpdate ? chosen && <p className="hint">{L('Role', '角色')}: {L(chosen.en, chosen.zh)}</p> : <div className="wb-role-choices" role="group" aria-label={L('Place role', '设施角色')}>
      {PLACE_ROLES.map(row => <button type="button" key={row.role} aria-pressed={role === row.role} className={`wb-btn small${role === row.role ? ' primary' : ''}`}
        onClick={() => { setRole(row.role); if (!touched.has('id')) setIdText(nextRoleId(row.role, entities.map(entity => entity.id))); }}>{L(row.en, row.zh)}</button>)}
    </div>}
    <div className="wb-add-grid">
      <label className="wb-field"><span>{L('id', 'id')}</span><input className="wb-mono" value={idText} readOnly={isUpdate} aria-invalid={touched.has('id') && idBad}
        onChange={event => { setIdText(event.target.value); mark('id'); }} /></label>
      <label className="wb-field"><span>{L('Name (English)', '名称（英文）')}</span><input value={labelEn} onChange={event => { setLabelEn(event.target.value); mark('label'); }} /></label>
      <label className="wb-field"><span>{L('Name (Chinese)', '名称（中文）')}</span><input value={labelZh} onChange={event => { setLabelZh(event.target.value); mark('label_zh'); }} /></label>
    </div>
    {touched.has('id') && idBad && <p className="wb-field-error" role="alert">{L('Enter a unique id using letters, digits and . _ : / - without spaces.', '请输入唯一 id，只能使用字母、数字和 . _ : / -，不能包含空格。')}</p>}
    {labelInvalid && <p className="wb-field-error" role="alert">{L('An existing name cannot be empty. Enter a name to continue.', '已有名称不能清空。请输入名称以继续。')}</p>}
    <div className="wb-add-grid">
      <label className="wb-field"><span>{L('position', '位置')}</span><select value={mode} onChange={event => { setMode(event.target.value as PositionMode); mark('position'); }}>
        <option value="centre">{L('Map centre (ENU origin)', '地图中心（ENU 原点）')}</option>
        <option value="facility" disabled={!facilities.length}>{L('Same point as an existing facility', '与已有设施同点')}</option>
        <option value="enu">{L('ENU metres (east, north, up)', 'ENU 米（东、北、天）')}</option>
      </select></label>
      {mode === 'facility' && <label className="wb-field"><span>{L('existing facility', '已有设施')}</span><select value={facilityId} disabled={!facilities.length}
        onChange={event => { setFacilityId(event.target.value); mark('facility'); }}>
        <option value="">{L('Choose…', '选择…')}</option>{facilities.map(entity => <option key={entity.id} value={entity.id}>{L(entity.label ?? entity.id, entity.label_zh ?? entity.label ?? entity.id)}</option>)}
      </select></label>}
      {mode === 'enu' && [[east, setEast, L('east (m)', '东向 (m)')], [north, setNorth, L('north (m)', '北向 (m)')], [up, setUp, L('up (m)', '天向 (m)')]].map(([raw, setRaw, label], index) => <label className="wb-field" key={index}>
        <span>{label as string}</span><input inputMode="decimal" value={raw as string} aria-invalid={touched.has('position') && numbers[index] === undefined}
          onChange={event => { (setRaw as (value: string) => void)(event.target.value); mark('position'); }} />
      </label>)}
    </div>
    {positionInvalid && (touched.has('position') || isUpdate) && <p className="wb-field-error" role="alert">{L('Enter three finite numbers for the position.', '请输入三个有限数值作为位置。')}</p>}
    {startInvalid && touched.has('facility') && <p className="wb-field-error" role="alert">{L('Choose an existing facility.', '请选择已有设施。')}</p>}
    <button type="button" className="wb-btn small" aria-expanded={picking} aria-pressed={picking} disabled={!canPick} onClick={() => setPicking(previous => !previous)}>
      {picking ? L('Close pick on map', '关闭地图选点') : L('Pick on map', '地图选点')}</button>
    {!canPick && <p className="hint">{L('Apply a region in Scene & space to pick a position on the map.', '在“场景与空间”应用区域后即可在地图上选点。')}</p>}
    {/* TODO R2-MAP: replace this RegionMap mount with the exported PlacePickerMap; pass project, draft, marker and onPick. */}
    {picking && canPick && <RegionMap L={L} height={260} basemap={null} coverage={region?.region?.bounds ?? null} geometry={mapGeometry}
      pick={{ marker, onPick: (pickedEast, pickedNorth) => { setMode('enu'); setEast(String(pickedEast)); setNorth(String(pickedNorth)); mark('position'); } }}
      outsideWarning={L('Outside the applied region.', '超出已应用区域。')} />}
    <div className="wb-add-grid">
      {hasCapacity && <label className="wb-field"><span>{L('capacity (parallel slots)', '容量（并行位）')}</span><input inputMode="numeric" value={capacity} aria-invalid={touched.has('capacity') && capacityInvalid}
        onChange={event => { setCapacity(event.target.value); mark('capacity'); }} /></label>}
      {hasPreparation && <label className="wb-field"><span>{L('preparation time (s)', '备餐时间 (s)')}</span><input inputMode="decimal" value={preparation} aria-invalid={touched.has('preparation') && preparationInvalid}
        onChange={event => { setPreparation(event.target.value); mark('preparation'); }} /></label>}
      {hasPower && <label className="wb-field"><span>{L('charging power (W)', '充电功率 (W)')}</span><input inputMode="decimal" value={power} aria-invalid={touched.has('power') && powerInvalid}
        onChange={event => { setPower(event.target.value); mark('power'); }} /></label>}
    </div>
    {capacityInvalid && touched.has('capacity') && <p className="wb-field-error" role="alert">{L('Capacity must be a whole number from 1 to 1000.', '容量必须是 1 到 1000 之间的整数。')}</p>}
    {((preparationInvalid && touched.has('preparation')) || (powerInvalid && touched.has('power'))) && <p className="wb-field-error" role="alert">{L('Enter a positive number.', '请输入正数。')}</p>}
    {review.preview && <OperationDiff {...review.preview} stale={review.stale} L={L} />}
    {review.problem && <p className="wb-field-error" role="alert">{review.problem.field && <span className="wb-mono">{review.problem.field}: </span>}{operationText(review.problem.message, L)}</p>}
    <div className="wb-object-actions">
      <button className="wb-btn" disabled={!canPreview || !!review.busy} onClick={() => void review.load()}>{review.busy === 'preview' ? L('Previewing…', '预览中…') : review.preview ? L('Preview again', '重新预览') : L('Preview', '预览')}</button>
      {review.preview && <button className="wb-btn primary" disabled={!canPreview || review.stale || !!review.busy} onClick={() => void apply()}>{review.busy === 'apply' ? L('Applying…', '应用中…') : L('Apply', '应用')}</button>}
      <button className="wb-btn ghost" disabled={!!review.busy} onClick={onClose}>{L('Close', '关闭')}</button>
    </div>
  </div>;
}
