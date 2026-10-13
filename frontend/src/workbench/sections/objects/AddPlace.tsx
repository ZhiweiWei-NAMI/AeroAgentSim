import { useMemo, useState } from 'react';
import { applyOperations, operation, operationProblem, previewOperations, type OperationPreview } from '../operations';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { ID_PATTERN, num } from './model';
import { RegionMap, type RegionMapBasemap, type RegionMapGeometry } from '../space/RegionMap';
import { readRegion } from '../space/model';

/** Server vocabulary (GET /v1/studio/operations): role → what the C11 compiler models. */
const PLACE_ROLES = [
  { role: 'vertiport', en: 'Vertiport', zh: '起降场', dot: '#1677ff', extra: 'capacity' },
  { role: 'charging_pad', en: 'Charging pad', zh: '充电位', dot: '#1fae6b', extra: 'power_w' },
  { role: 'depot', en: 'Depot', zh: '仓库', dot: '#e8a317', extra: 'capacity' },
  { role: 'kitchen', en: 'Restaurant (kitchen)', zh: '餐厅（厨房）', dot: '#e0453a', extra: 'preparation' },
  { role: 'customer', en: 'Customer', zh: '客户', dot: '#b26bff', extra: 'capacity' },
  { role: 'handoff_point', en: 'Handoff point', zh: '交接点', dot: '#38c6f4', extra: 'none' },
  { role: 'incident_site', en: 'Incident site', zh: '事件地点', dot: '#ff7a45', extra: 'none' },
  { role: 'inspection_start', en: 'Inspection start', zh: '巡检起点', dot: '#5b6b7d', extra: 'none' },
  { role: 'inspection_end', en: 'Inspection end', zh: '巡检终点', dot: '#5b6b7d', extra: 'none' },
] as const;
type Extra = (typeof PLACE_ROLES)[number]['extra'];

type PositionMode = 'centre' | 'facility' | 'enu' | 'map';

/** Add or update a low-altitude place through the C11 operations API only.
 * Every action is gated on its prerequisite, shown inline; the server diff is the review. */
export function AddPlace({ api, project, draft, project_detail, onProject, L, onClose, editing }: {
  api: WorkbenchApi; project: Project; draft: Scenario;
  /** Full workspace (region + compiled scene), used only for the pick-on-map origin. */
  project_detail?: { region?: unknown; scene?: unknown };
  onProject: (project: Project) => void; L: (en: string, zh: string) => string; onClose: () => void;
  editing?: { id: string; capacity?: number; preparation_s?: number; power_w?: number };
}) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const facilities = entities.filter(entity => entity.facts && ('lowalt.site_position' in entity.facts || 'lowalt.position' in entity.facts));
  const isUpdate = !!editing;
  const [role, setRole] = useState<(typeof PLACE_ROLES)[number]['role'] | ''>(isUpdate ? '' : '');
  const [idText, setIdText] = useState(editing?.id ?? '');
  const [labelEn, setLabelEn] = useState('');
  const [labelZh, setLabelZh] = useState('');
  const [mode, setMode] = useState<PositionMode>(editing?.position ? 'enu' : 'centre');
  const [facilityId, setFacilityId] = useState('');
  const [east, setEast] = useState(editing?.position ? String(editing.position[0] ?? 0) : '0');
  const [north, setNorth] = useState(editing?.position ? String(editing.position[1] ?? 0) : '0');
  const [up, setUp] = useState(editing?.position ? String(editing.position[2] ?? 0) : '0');
  const [capacity, setCapacity] = useState(editing?.capacity !== undefined ? String(editing.capacity) : '');
  const [preparation, setPreparation] = useState(editing?.preparation_s !== undefined ? String(editing.preparation_s) : '');
  const [power, setPower] = useState(editing?.power_w !== undefined ? String(editing.power_w) : '');
  const [picking, setPicking] = useState(false);
  const [pickMarker, setPickMarker] = useState<[number, number] | null>(null);
  const [preview, setPreview] = useState<OperationPreview>();
  const [previewStale, setPreviewStale] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'apply' | undefined>();
  const [problem, setProblem] = useState<{ field?: string; message: string }>();

  const chosen = PLACE_ROLES.find(row => row.role === role);
  const idBad = !isUpdate && (!idText.trim() || !ID_PATTERN.test(idText.trim()) ||
    entities.some(entity => entity.id === idText.trim()));
  const numbers = useMemo(() => ({ east: num(east), north: num(north), up: num(up) }), [east, north, up]);
  const positionInvalid = mode === 'enu' && Object.values(numbers).some(value => value === undefined);
  const capacityInvalid = capacity.trim() !== '' && (num(capacity) === undefined || !Number.isInteger(Number(capacity)) || Number(capacity) < 1);
  const preparationInvalid = preparation.trim() !== '' && num(preparation) === undefined;
  const powerInvalid = power.trim() !== '' && num(power) === undefined;
  const extra: Extra = chosen?.extra ?? 'none';
  const roleMissing = !isUpdate && !chosen;

  const at = mode === 'centre' ? 'centre'
    : mode === 'facility' ? facilityId
    : [numbers.east, numbers.north, numbers.up];
  const canPreview = !roleMissing && !idBad && !positionInvalid && !capacityInvalid && !preparationInvalid && !powerInvalid
    && (mode !== 'facility' || !!facilityId);

  const params = () => {
    const body: Record<string, unknown> = { id: idText.trim() };
    if (!isUpdate) body.role = role;
    if (mode !== 'map') body.at = at;
    if (labelEn.trim() !== '') body.label = labelEn.trim();
    if (labelZh.trim() !== '') body.label_zh = labelZh.trim();
    if (extra === 'capacity' && capacity.trim() !== '') body.capacity = Number(capacity);
    if (extra === 'preparation' && preparation.trim() !== '') body.preparation_s = Number(preparation);
    if (extra === 'power_w' && power.trim() !== '') body.power_w = Number(power);
    return body;
  };

  // Any input change marks the last preview out of date; Apply stays disabled until re-previewed.
  const touch = () => { setPreviewStale(!!preview); };
  const load = async () => {
    if (!canPreview) return;
    setBusy('preview'); setProblem(undefined);
    try {
      const answer = await previewOperations(api, project, [operation(isUpdate ? 'update_place' : 'add_place', params())]);
      setPreview(answer); setPreviewStale(false);
    } catch (failure) { setProblem(operationProblem(failure)); setPreview(undefined); }
    finally { setBusy(undefined); }
  };
  const apply = async () => {
    if (!preview || previewStale) return;
    setBusy('apply'); setProblem(undefined);
    try {
      const updated = await applyOperations(api, project, [operation(isUpdate ? 'update_place' : 'add_place', params())]);
      onProject(updated); onClose();
    } catch (failure) { setProblem(operationProblem(failure)); setPreviewStale(true); }
    finally { setBusy(undefined); }
  };

  // Pick-on-map needs an applied region (a scenario origin + compiled scene). C9/C10.
  const region = useMemo(() => readRegion(project_detail as never), [project_detail]);
  const mapOrigin = region?.scene?.origin ?? (draft.origin as { lat: number; lon: number; alt: number } | undefined) ?? null;
  const mapGeometry: RegionMapGeometry | null = mapOrigin ? { origin: mapOrigin } : null;
  const mapBasemap: RegionMapBasemap | null = null;
  const canPick = !!mapOrigin && !!region?.region;

  return <div style={{ border: '1px solid var(--wb-line)', borderRadius: 12, padding: '10px 12px', marginBottom: 12 }} data-testid="wb-add-place">
    <h4 style={{ margin: '2px 0 4px' }}>{isUpdate ? L('Edit place', '编辑设施') : L('Add place', '添加设施')}</h4>
    <p className="hint">{L('The server owns low-altitude structures: this form sends a C11 operation, previews its dry-run diff, and applies only that.', '低空结构由服务器负责：此表单发送 C11 操作，先预览预演差异，再只应用该差异。')}</p>
    {!isUpdate && <>
      <label className="hint" style={{ display: 'block', margin: '4px 0 2px' }}>{L('Role (what the server will model)', '角色（服务器将建模的内容）')}</label>
      <div role="radiogroup" aria-label={L('Place role', '设施角色')} style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {PLACE_ROLES.map(row => <button key={row.role} role="radio" aria-checked={role === row.role} className="wb-chip" style={{ cursor: 'pointer' }}
          onClick={() => { setRole(row.role); touch(); }}>
          <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, background: row.dot, display: 'inline-block' }} />
          {L(row.en, row.zh)}
        </button>)}
      </div>
      {roleMissing && <p className="wb-add-note" role="status">{L('Choose a role to continue — the role decides which fields the server models.', '请先选择角色——角色决定服务器建模哪些字段。')}</p>}
    </>}
    <div className="wb-add-grid">
      <label className="wb-field"><span>{L('id', 'id')}</span>
        <input className="wb-mono" value={idText} readOnly={isUpdate} aria-invalid={idBad}
          onChange={event => { setIdText(event.target.value); touch(); }} placeholder="charging-pad-1" /></label>
      <label className="wb-field"><span>{L('label (en)', '名称（英文）')}</span>
        <input value={labelEn} onChange={event => { setLabelEn(event.target.value); touch(); }} placeholder={chosen ? L(chosen.en, chosen.zh) : ''} /></label>
      <label className="wb-field"><span>{L('名称（中文）', '名称（中文）')}</span>
        <input value={labelZh} onChange={event => { setLabelZh(event.target.value); touch(); }} placeholder={chosen?.zh ?? ''} /></label>
    </div>
    {idBad && <p className="wb-field-error" role="alert">{idText.trim()
      ? L('Use letters, digits and . _ : / - with no spaces, and an id no instance already uses.', '只能使用字母、数字和 . _ : / -，且不能与已有实例重复。')
      : L('An id is required.', '必须提供 id。')}</p>}
    <div className="wb-add-grid">
      <label className="wb-field"><span>{L('position', '位置')}</span>
        <select value={mode} onChange={event => { setMode(event.target.value as PositionMode); touch(); }}>
          <option value="centre">{L('Map centre (ENU origin)', '地图中心（ENU 原点）')}</option>
          <option value="facility">{L('Same point as an existing facility', '与已有设施同点')}</option>
          <option value="enu">{L('ENU metres (east / north / up)', 'ENU 米（东 / 北 / 天）')}</option>
          <option value="map" disabled={!canPick}>{L('Pick on map', '地图选点')}</option>
        </select></label>
      {mode === 'facility' && <label className="wb-field"><span>{L('existing facility', '已有设施')}</span>
        <select value={facilityId} onChange={event => { setFacilityId(event.target.value); touch(); }}>
          <option value="">{L('Choose…', '选择…')}</option>
          {facilities.map(entity => <option key={entity.id} value={entity.id}>{entity.id}</option>)}
        </select></label>}
      {mode === 'enu' && <>
        <label className="wb-field"><span>{L('east (m)', '东向 (m)')}</span><input inputMode="decimal" value={east} aria-invalid={num(east) === undefined} onChange={event => { setEast(event.target.value); touch(); }} /></label>
        <label className="wb-field"><span>{L('north (m)', '北向 (m)')}</span><input inputMode="decimal" value={north} aria-invalid={num(north) === undefined} onChange={event => { setNorth(event.target.value); touch(); }} /></label>
        <label className="wb-field"><span>{L('up (m)', '天向 (m)')}</span><input inputMode="decimal" value={up} aria-invalid={num(up) === undefined} onChange={event => { setUp(event.target.value); touch(); }} /></label>
      </>}
      <div className="wb-field" style={{ display: 'flex', alignItems: 'flex-end' }}>
        <button type="button" className="wb-btn small" aria-expanded={picking} aria-pressed={picking} disabled={!canPick}
          title={canPick ? undefined : L('Apply a region first (Scene & space › Region)', '请先应用区域（场景与空间 › 区域）')}
          onClick={() => { setPicking(previous => !previous); setMode('map'); }}>
          {picking ? L('Close pick on map', '关闭地图选点') : L('Pick on map', '地图选点')}</button>
      </div>
    </div>
    {mode === 'enu' && positionInvalid && <p className="wb-field-error" role="alert">{L('Enter finite numbers; negative metres are allowed.', '请输入有限数值；负数米值是允许的。')}</p>}
    {mode === 'facility' && !facilityId && <p className="wb-field-error" role="alert">{L('Choose which facility this place shares a point with.', '请选择与该设施共点的已有设施。')}</p>}
    {!canPick && <p className="wb-add-note">{L('"Pick on map" needs an applied region (Scene & space › Region).', '“地图选点”需要已应用区域（场景与空间 › 区域）。')}</p>}
    {picking && mode === 'map' && <RegionMap
      L={L} height={260} basemap={mapBasemap}
      coverage={region?.region?.bounds ?? null} geometry={mapGeometry}
      pick={{ onPick: (pickedEast, pickedNorth) => {
        setPickMarker([pickedEast, pickedNorth]);
        setMode('enu');
        setEast((Math.round(pickedEast * 10) / 10).toString());
        setNorth((Math.round(pickedNorth * 10) / 10).toString());
        setUp('0');
      }, marker: pickMarker }}
      outsideWarning={L('Outside the applied region — allowed, but the point sits outside the compiled area.', '在已应用区域之外——允许，但该点位于已编译范围外。')} />}
    {extra === 'capacity' && <div className="wb-add-grid">
      <label className="wb-field"><span>{L('capacity (parallel slots)', '容量（并行位）')}</span>
        <input inputMode="numeric" value={capacity} aria-invalid={capacityInvalid} placeholder="1"
          onChange={event => { setCapacity(event.target.value); touch(); }} /></label>
    </div>}
    {capacityInvalid && <p className="wb-field-error" role="alert">{L('Capacity must be a whole number ≥ 1.', '容量必须是不小于 1 的整数。')}</p>}
    {extra === 'preparation' && <div className="wb-add-grid">
      <label className="wb-field"><span>{L('preparation time (s)', '备餐时间 (s)')}</span>
        <input inputMode="decimal" value={preparation} aria-invalid={preparationInvalid} placeholder="30"
          onChange={event => { setPreparation(event.target.value); touch(); }} /></label>
    </div>}
    {extra === 'power_w' && <div className="wb-add-grid">
      <label className="wb-field"><span>{L('charging power (W)', '充电功率 (W)')}</span>
        <input inputMode="decimal" value={power} aria-invalid={powerInvalid} placeholder="5000"
          onChange={event => { setPower(event.target.value); touch(); }} /></label>
    </div>}
    {(preparationInvalid || powerInvalid) && <p className="wb-field-error" role="alert">{L('Enter a positive number.', '请输入正数。')}</p>}
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '8px 0 2px' }}>
      <button className="wb-btn" disabled={!canPreview || busy !== undefined} onClick={() => void load()}>
        {busy === 'preview' ? L('Previewing…', '预览中…') : preview ? L('Re-preview', '重新预览') : L('Preview dry-run diff', '预览预演差异')}</button>
      {preview && <button className="wb-btn primary" disabled={previewStale || busy !== undefined} onClick={() => void apply()}>
        {busy === 'apply' ? L('Applying…', '应用中…') : L('Apply', '应用')}</button>}
      <button className="wb-btn ghost" disabled={busy !== undefined} onClick={onClose}>{L('Close', '关闭')}</button>
      {!canPreview && <span className="wb-add-note">{roleMissing
        ? L('Choose a role first.', '请先选择角色。')
        : L('Fix the marked fields to enable the preview.', '修正标记的字段后才能预览。')}</span>}
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
      {preview.notes.map((note, index) => <p key={index} className="hint" style={{ margin: '2px 0' }}>
        {typeof note === 'string' ? note : note.en ?? note.zh ?? note.text}</p>)}
    </div>}
    {problem && <p role="alert" style={{ color: 'var(--wb-bad)', margin: '6px 0 0' }}>
      {problem.field ? <><span className="wb-mono">{problem.field}</span>: </> : null}{problem.message}</p>}
  </div>;
}
