import { operation, useWorkspaceOperations } from '../operations';
import { useStagedForm } from '../useStagedForm';
import { StagedFormNote } from '../tasks/op-ui';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { ID_PATTERN, nextRoleId, num, PLACE_ROLES, placeRoleOf, strictVector3, type PlaceEditing, type PlaceRole } from './model';
import { OperationDiff, operationText, useOperationsReview } from './Operations';
import { PlacePickerMap, canPickOnMap } from '../space/PlacePickerMap';

type PositionMode = 'map' | 'typed' | 'facility';
interface PlaceFields {
  role?: PlaceRole; idText: string; labelEn: string; labelZh: string; mode: PositionMode; facilityId: string;
  east: string; north: string; up: string; capacity: string; preparation: string; power: string; touched: string[];
}

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
  const workspace = project_detail ?? project;
  const canPick = canPickOnMap(workspace);
  const savedPosition = strictVector3(editing?.position) ? editing.position : undefined;
  const staged = useStagedForm<PlaceFields>(project.id, isUpdate ? `place.edit:${editing.id}` : 'place.add', {
    role: isUpdate ? savedRole : initialRole,
    idText: editing?.id ?? nextRoleId(initialRole, entities.map(entity => entity.id)),
    labelEn: editing?.label ?? '', labelZh: editing?.label_zh ?? '',
    mode: isUpdate || !canPick ? 'typed' : 'map', facilityId: '',
    east: savedPosition ? String(savedPosition[0]) : isUpdate ? '' : '0',
    north: savedPosition ? String(savedPosition[1]) : isUpdate ? '' : '0',
    up: savedPosition ? String(savedPosition[2]) : isUpdate ? '' : '0',
    // Documented C11 defaults are explicit values for new places.
    capacity: editing?.capacity !== undefined ? String(editing.capacity) : isUpdate ? '' : '1',
    preparation: editing?.preparation_s !== undefined ? String(editing.preparation_s) : isUpdate ? '' : '30',
    power: editing?.power_w !== undefined ? String(editing.power_w) : isUpdate ? '' : '5000', touched: [],
  }, project.revision);
  const { role, idText, labelEn, labelZh, mode, facilityId, east, north, up, capacity, preparation, power } = staged.values;
  const touched = new Set(staged.values.touched);
  const set = <K extends keyof PlaceFields>(field: K, value: PlaceFields[K]) => staged.setValues(previous => ({ ...previous, [field]: value }));
  const mark = (field: string) => staged.setValues(previous => ({ ...previous, touched: [...new Set([...previous.touched, field])] }));
  const setRole = (value: PlaceRole) => set('role', value), setIdText = (value: string) => set('idText', value);
  const setLabelEn = (value: string) => set('labelEn', value), setLabelZh = (value: string) => set('labelZh', value);
  const setMode = (value: PositionMode) => set('mode', value);
  const setEast = (value: string) => set('east', value), setNorth = (value: string) => set('north', value), setUp = (value: string) => set('up', value);
  const setCapacity = (value: string) => set('capacity', value), setPreparation = (value: string) => set('preparation', value), setPower = (value: string) => set('power', value);
  const copyFacility = (id: string) => {
    const position = facilities.find(entity => entity.id === id)?.facts['lowalt.site_position'];
    staged.setValues(previous => ({ ...previous, mode: 'facility', facilityId: id,
      ...(strictVector3(position) ? { east: String(position[0]), north: String(position[1]), up: String(position[2]) } : {}),
      touched: [...new Set([...previous.touched, 'facility'])],
    }));
  };
  const chosen = PLACE_ROLES.find(row => row.role === role);
  const hasCapacity = role !== 'handoff_point';
  const hasPreparation = role === 'kitchen' || (isUpdate && editing.preparation_s !== undefined);
  const hasPower = role === 'charging_pad' || (isUpdate && editing.power_w !== undefined);
  const idBad = !idText.trim() || !ID_PATTERN.test(idText.trim()) || (!isUpdate && entities.some(entity => entity.id === idText.trim()));
  const numbers = [num(east), num(north), num(up)];
  const positionInvalid = numbers.some(value => value === undefined);
  const capacityValue = num(capacity), preparationValue = num(preparation), powerValue = num(power);
  const cleared = (field: string, raw: string, current: unknown) => isUpdate && touched.has(field) && raw.trim() === '' && current !== undefined;
  const capacityInvalid = hasCapacity && (capacity.trim() !== '' && (capacityValue === undefined || !Number.isInteger(capacityValue) || capacityValue < 1 || capacityValue > 1000) || cleared('capacity', capacity, editing?.capacity));
  const preparationInvalid = hasPreparation && (preparation.trim() !== '' && (preparationValue === undefined || preparationValue <= 0) || cleared('preparation', preparation, editing?.preparation_s));
  const powerInvalid = hasPower && (power.trim() !== '' && (powerValue === undefined || powerValue <= 0) || cleared('power', power, editing?.power_w));
  const labelInvalid = cleared('label', labelEn, editing?.label) || cleared('label_zh', labelZh, editing?.label_zh);
  const at = numbers;
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
  const workspaceOperations = useWorkspaceOperations(project);
  const review = useOperationsReview(api, workspaceOperations.project, [operation(isUpdate ? 'update_place' : 'add_place', params)]);
  const apply = async () => { if (!canPreview || workspaceOperations.applying) return; const updated = await review.apply(); if (updated) { staged.clear(staged.values); onProject(updated); onClose(); } };

  const marker: [number, number] | null = numbers[0] !== undefined && numbers[1] !== undefined ? [numbers[0], numbers[1]] : null;

  return <div className="wb-object-form wb-form-card" data-testid="wb-add-place">
    <h4 className="wb-form-card-title">{isUpdate ? L('Edit place', '编辑设施') : L('Add place', '添加设施')}</h4>
    <p className="wb-form-card-sub">{L('Set the place and its service settings, then review the changes before applying.', '设置设施及服务参数，查看变更后再应用。')}</p>
    <StagedFormNote form={staged} L={L} onDiscard={review.clear} />
    {isUpdate ? chosen && <p className="wb-form-field"><span className="wb-label">{L('Role', '角色')}: {L(chosen.en, chosen.zh)}</span></p>
      : <div className="wb-choice-group" role="group" aria-label={L('Place role', '设施角色')}>
      {PLACE_ROLES.map(row => <button type="button" key={row.role} aria-pressed={role === row.role} className="wb-choice"
        onClick={() => { setRole(row.role); if (!touched.has('id')) setIdText(nextRoleId(row.role, entities.map(entity => entity.id))); }}>{L(row.en, row.zh)}</button>)}
    </div>}
    {canPick ? <div className="wb-place-picker" data-testid="wb-place-picker">
      <PlacePickerMap api={api} workspace={workspace} draft={draft} L={(en, zh) => en === 'Click to place — east/north fill in automatically' ? L('Click the map to place it', '点击地图放置') : L(en, zh)} position={marker}
        onPick={(pickedEast, pickedNorth) => { setMode('map'); setEast(pickedEast.toFixed(1)); setNorth(pickedNorth.toFixed(1)); mark('position'); }} />
    </div>
      : <p className="wb-form-prerequisite">{L('Apply a region in Scene & space to pick a position on the map.', '在“场景与空间”应用区域后即可在地图上选点。')}</p>}
    <div className="wb-form-grid">
      <label className="wb-form-field"><span className="wb-label">{L('East (m)', '东（m）')}</span><input inputMode="decimal" value={east} aria-invalid={touched.has('position') && numbers[0] === undefined}
        onChange={event => { setMode('typed'); setEast(event.target.value); mark('position'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('North (m)', '北（m）')}</span><input inputMode="decimal" value={north} aria-invalid={touched.has('position') && numbers[1] === undefined}
        onChange={event => { setMode('typed'); setNorth(event.target.value); mark('position'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('Height (m)', '高度（m）')}</span><input inputMode="decimal" value={up} aria-invalid={touched.has('position') && numbers[2] === undefined}
        onChange={event => { setMode('typed'); setUp(event.target.value); mark('position'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('Position from', '位置来源')}</span><select value={mode} onChange={event => { const source = event.target.value as PositionMode; if (source === 'facility' && facilityId) copyFacility(facilityId); else setMode(source); mark('position'); }}>
        <option value="map" disabled={!canPick}>{L('Map click', '地图选点')}</option>
        <option value="typed">{L('Typed', '手动输入')}</option>
        <option value="facility" disabled={!facilities.length}>{L('Existing facility', '已有设施')}</option>
      </select></label>
    </div>
    {touched.has('id') && idBad && <p className="wb-field-error" role="alert">{L('Enter a unique id using letters, digits and . _ : / - without spaces.', '请输入唯一 id，只能使用字母、数字和 . _ : / -，不能包含空格。')}</p>}
    {labelInvalid && <p className="wb-field-error" role="alert">{L('An existing name cannot be empty. Enter a name to continue.', '已有名称不能清空。请输入名称以继续。')}</p>}
    <div className="wb-form-grid">
      <label className="wb-form-field" style={{ gridColumn: '1 / -1' }}><span className="wb-label">{L('Id', 'Id')}</span><input className="wb-mono" value={idText} readOnly={isUpdate} aria-invalid={touched.has('id') && idBad}
        onChange={event => { setIdText(event.target.value); mark('id'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('Name (English)', '名称（英文）')}</span><input value={labelEn} onChange={event => { setLabelEn(event.target.value); mark('label'); }} /></label>
      <label className="wb-form-field"><span className="wb-label">{L('Name (Chinese)', '名称（中文）')}</span><input value={labelZh} onChange={event => { setLabelZh(event.target.value); mark('label_zh'); }} /></label>
    </div>
    {mode === 'facility' && <div className="wb-form-grid">
      <label className="wb-form-field"><span className="wb-label">{L('Existing facility', '已有设施')}</span><select value={facilityId} disabled={!facilities.length}
        onChange={event => { copyFacility(event.target.value); }}>
        <option value="">{L('Choose…', '选择…')}</option>{facilities.map(entity => <option key={entity.id} value={entity.id}>{L(entity.label ?? entity.id, entity.label_zh ?? entity.label ?? entity.id)}</option>)}
      </select></label>
    </div>}
    {positionInvalid && (touched.has('position') || isUpdate) && <p className="wb-field-error" role="alert">{L('Enter three finite numbers for the position.', '请输入三个有限数值作为位置。')}</p>}
    {startInvalid && <p className="hint wb-form-prerequisite" role="note">{L('Choose an existing facility.', '请选择已有设施。')}</p>}
    <div className="wb-form-grid">
      {hasCapacity && <label className="wb-form-field"><span className="wb-label">{L('capacity (parallel slots)', '容量（并行位）')}</span><input inputMode="numeric" value={capacity} aria-invalid={touched.has('capacity') && capacityInvalid}
        onChange={event => { setCapacity(event.target.value); mark('capacity'); }} /></label>}
      {hasPreparation && <label className="wb-form-field"><span className="wb-label">{L('preparation time (s)', '备餐时间 (s)')}</span><input inputMode="decimal" value={preparation} aria-invalid={touched.has('preparation') && preparationInvalid}
        onChange={event => { setPreparation(event.target.value); mark('preparation'); }} /></label>}
      {hasPower && <label className="wb-form-field"><span className="wb-label">{L('charging power (W)', '充电功率 (W)')}</span><input inputMode="decimal" value={power} aria-invalid={touched.has('power') && powerInvalid}
        onChange={event => { setPower(event.target.value); mark('power'); }} /></label>}
    </div>
    {capacityInvalid && touched.has('capacity') && <p className="wb-field-error" role="alert">{L('Capacity must be a whole number from 1 to 1000.', '容量必须是 1 到 1000 之间的整数。')}</p>}
    {((preparationInvalid && touched.has('preparation')) || (powerInvalid && touched.has('power'))) && <p className="wb-field-error" role="alert">{L('Enter a positive number.', '请输入正数。')}</p>}
    {review.preview && <OperationDiff {...review.preview} stale={review.stale} L={L} />}
    {review.problem && <p className="wb-field-error" role="alert">{review.problem.field && <span className="wb-mono">{review.problem.field}: </span>}{operationText(review.problem.message, L)}</p>}
    {workspaceOperations.applying && review.busy !== 'apply' && <p className="hint wb-form-prerequisite" role="note">{L('Applying another change…', '正在应用另一项变更…')}</p>}
    <div className="wb-form-actions wb-object-actions">
      <button className="wb-btn" disabled={!canPreview || !!review.busy || workspaceOperations.applying} onClick={() => void review.load()}>{review.busy === 'preview' ? L('Previewing…', '预览中…') : review.preview ? L('Preview again', '重新预览') : L('Preview', '预览')}</button>
      {review.preview && <button className="wb-btn primary" disabled={!canPreview || review.stale || !!review.busy || workspaceOperations.applying} onClick={() => void apply()}>{review.busy === 'apply' ? L('Applying…', '应用中…') : L('Apply', '应用')}</button>}
      <button className="wb-btn" disabled={!!review.busy} onClick={onClose}>{L('Close', '关闭')}</button>
    </div>
  </div>;
}
