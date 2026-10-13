import type { StudioApi } from '../../../studio/api';
import { useMemo, useRef, useState } from 'react';
import { ApiError, type Project, type WorkbenchApi } from '../../api';
import type { TypeDetail } from '../../../studio/api';
import type { Scenario } from '../types';
import { airspaceFieldOf, ID_PATTERN, isLowaltType, KINEMATIC_DEFAULTS, num, parseFact, positionFieldOf, STUDIO_AIRSPACE, STUDIO_STATE, useCatalog, WORKFLOW_DEFAULT_STATE, type CatalogType } from './model';
import { operationText } from './Operations';
import { RegionMap, type RegionMapBasemap, type RegionMapGeometry } from '../space/RegionMap';
import { readRegion } from '../space/model';

type Kind = 'entity' | 'facility' | 'airspace';
type Engine = 'kinematic' | 'workflow';
type Row = { id: string; type: string; kind: Kind; engine: Engine; position: number[]; facts: Record<string, unknown> };

const ID_RULE = (L: (en: string, zh: string) => string) => L('Allowed: letters, digits and . _ : / - — no spaces or *', '允许：字母、数字和 . _ : / - — 不含空格或 *');
const factErrorText = (error: string, L: (en: string, zh: string) => string) => {
  const count = /^(\d+) values$/.exec(error)?.[1];
  if (count) return L(`${count} numeric values`, `${count} 个数值`);
  return error === 'number' ? L('a finite number', '有限数值') : error === 'integer' ? L('a whole number', '整数')
    : error === 'numbers' ? L('finite numbers', '有限数值') : error === 'true | false' ? L('true or false', 'true 或 false') : error;
};

/** The staged "Add objects" flow: type → identity/kind → placement/assumptions → review → one submit. */
export function AddObjects({ api, studioApi, project, draft, project_detail, onProject, L, onClose, saveDraft }: {
  api: WorkbenchApi; studioApi: StudioApi; project: { id: string; revision: number }; draft: Scenario;
  /** Full workspace (region + compiled scene), used only for the pick-on-map origin. */
  project_detail?: { region?: unknown; scene?: unknown };
  onProject: (project: Project) => void; L: (en: string, zh: string) => string; onClose: () => void;
  saveDraft?: () => Promise<Project | undefined>;
}) {
  const { catalog, catalogError } = useCatalog(studioApi, project.id, true);
  const [typeQuery, setTypeQuery] = useState('');
  const [typeId, setTypeId] = useState<string>();
  const [detail, setDetail] = useState<TypeDetail>();
  const [detailError, setDetailError] = useState<string>();
  const selection = useRef(0);
  const [kind, setKind] = useState<Kind>('entity');
  const [engine, setEngine] = useState<Engine>('kinematic');
  const [idText, setIdText] = useState('');
  const [countText, setCountText] = useState('1');
  const count = Number(countText);
  const [east, setEast] = useState('0'), [north, setNorth] = useState('0'), [up, setUp] = useState('0');
  const [spacing, setSpacing] = useState('10');
  const [facts, setFacts] = useState<Record<string, string>>({});
  const [polygon, setPolygon] = useState('');
  const [floor, setFloor] = useState('0'), [ceiling, setCeiling] = useState('120');
  const [creating, setCreating] = useState(false);
  const [result, setResult] = useState<{ created: string[]; failed: Array<{ id: string; error: string }> }>();
  const [picking, setPicking] = useState(false);
  const [pickMarker, setPickMarker] = useState<[number, number] | null>(null);

  // Pick-on-map needs an applied region (a scenario origin + compiled scene). C9/C10.
  const region = useMemo(() => readRegion(project_detail as never), [project_detail]);
  const mapOrigin = region?.scene?.origin ?? (draft.origin as { lat: number; lon: number; alt: number } | undefined) ?? null;
  const mapGeometry: RegionMapGeometry | null = mapOrigin ? { origin: mapOrigin } : null;
  const mapBasemap: RegionMapBasemap | null = null; // AddObjects reuses the neutral surface; the Region block owns the tile layer.
  const canPick = !!mapOrigin && !!region?.region;

  const matches = useMemo(() => {
    if (!catalog) return [];
    const query = typeQuery.trim().toLowerCase();
    return catalog.types.filter(row => !query || row.id.toLowerCase().includes(query) || (row.name ?? '').toLowerCase().includes(query)).slice(0, 60);
  }, [catalog, typeQuery]);
  const chosen: CatalogType | undefined = catalog?.types.find(row => row.id === typeId);
  const regionField = airspaceFieldOf(detail?.fields ?? []);
  const contractKind = chosen?.kind ?? (regionField ? 'airspace' : undefined);
  const unsupportedRegion = (!!regionField || contractKind === 'airspace' || chosen?.id === STUDIO_AIRSPACE) && !!detail
    && (chosen?.id !== STUDIO_AIRSPACE || regionField?.id !== 'aas.studio.airspace' || !!chosen.kind && chosen.kind !== 'airspace');
  const regionReason = L('This contract needs a region editor; this form cannot author its declared geometry.', '此契约需要区域编辑器；本表单无法编辑它声明的几何字段。');
  const lowaltReason = L('Use Add place or Add fleet for this low-altitude contract.', '请通过“添加设施”或“添加机群”编辑此低空契约。');
  const abstractReason = L('Abstract types define shared fields only; the server rejects instantiating them.', '抽象类型只定义公共字段，服务器会拒绝实例化。');
  const facilityLike = kind === 'facility' || kind === 'airspace';
  const effectiveKind: Kind = kind === 'airspace' ? 'airspace' : facilityLike ? 'facility' : 'entity';
  const countInvalid = num(countText) === undefined || !Number.isInteger(count) || count < 1 || count > 500;
  // Position parsing keeps the raw text while typing and accepts negative metres (T4-02/T1-019).
  const posInvalid = kind !== 'airspace' && [east, north, up].some(raw => num(raw) === undefined);
  const spacingInvalid = count > 1 && num(spacing) === undefined;
  const idBase = idText.trim();

  const idFor = (index: number) => count === 1 ? idBase : `${idBase}-${index + 1}`;
  const duplicate = (id: string) => ((Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>).some(entity => entity.id === id);
  const idsBad = !idBase || !ID_PATTERN.test(idBase) || !countInvalid && Array.from({ length: count }, (_, index) => idFor(index)).some(duplicate);
  const firstIdClash = idBase && !countInvalid ? Array.from({ length: count }, (_, index) => idFor(index)).find(duplicate) : undefined;

  const chooseType = (row: CatalogType) => {
    const token = ++selection.current;
    setTypeId(row.id); setDetail(undefined); setDetailError(undefined); setResult(undefined); setFacts({});
    const fields = (catalog?.fields ?? []).filter(field => field.declaring_type === row.id || field.type === row.id);
    const nextKind = row.kind ?? (airspaceFieldOf(fields) ? 'airspace' : 'entity');
    setKind(nextKind); setEngine(row.engine ?? (nextKind === 'entity' ? 'kinematic' : 'workflow'));
    if (nextKind === 'airspace') setCountText('1');
    void studioApi.request<TypeDetail>(`/v1/studio/types/${row.id.split('/').map(encodeURIComponent).join('/')}?workspace=${encodeURIComponent(project.id)}`)
      .then(answer => {
        if (token !== selection.current) return;
        setDetail(answer);
        if (airspaceFieldOf(answer.fields)) { setKind(row.kind ?? 'airspace'); setEngine('workflow'); setCountText('1'); }
      }).catch(problem => { if (token === selection.current) setDetailError(String(problem)); });
  };
  const fieldRows = (detail?.fields ?? []).filter(field => field.id !== positionFieldOf(draft, chosen?.id ?? '') && field.id !== regionField?.id
    && (kind !== 'airspace' || !['aas.studio.velocity_enu_m_s', 'aas.studio.energy_j'].includes(field.id)));
  const defaultFor = (field: { id: string; schema: any; metadata: any }): string | undefined => {
    if (engine === 'kinematic' && field.id === 'aas.studio.velocity_enu_m_s') return '0, 0, 0';
    if (engine === 'kinematic' && field.id === 'aas.studio.energy_j') return String(KINEMATIC_DEFAULTS.energy);
    if (engine === 'workflow' && field.id === STUDIO_STATE) return WORKFLOW_DEFAULT_STATE;
    return undefined;
  };
  // Invalid fact input is a visible per-field error that blocks creation — it is never silently
  // dropped or replaced by a default (T1-006).
  const factErrors = useMemo(() => {
    const errors: Record<string, string> = {};
    for (const [key, raw] of Object.entries(facts) as Array<[string, string]>) {
      if (raw.trim() === '') continue;
      const field = fieldRows.find(candidate => candidate.id === key);
      const parsed = field ? parseFact(field.schema, raw) : { error: L('Field is not declared by this contract', '此契约没有声明该字段') };
      if (parsed.error !== undefined) errors[key] = parsed.error;
    }
    return errors;
  }, [facts, fieldRows]);
  const reviewRows: Row[] = useMemo(() => {
    if (!chosen || idsBad || countInvalid || posInvalid || spacingInvalid || Object.keys(factErrors).length) return [];
    // The legacy placement envelope needs an anchor; bounded regions use the workspace origin.
    const base = kind === 'airspace' ? [0, 0, 0] : [num(east)!, num(north)!, num(up)!];
    const step = count > 1 ? num(spacing)! : 0;
    return Array.from({ length: count }, (_, index) => {
      const rowFacts: Record<string, unknown> = {};
      for (const [key, raw] of Object.entries(facts) as Array<[string, string]>) {
        if (raw.trim() === '') continue;
        const field = fieldRows.find(candidate => candidate.id === key);
        if (!field) continue; // Creation is blocked above when the contract has no such field.
        const parsed = parseFact(field.schema, raw);
        if (parsed.value !== undefined) rowFacts[key] = parsed.value;
      }
      return { id: idFor(index), type: chosen.id, kind: effectiveKind, engine, position: [base[0] + index * step, base[1], base[2]], facts: rowFacts };
    });
  }, [chosen, idsBad, countInvalid, posInvalid, spacingInvalid, east, north, up, spacing, count, facts, factErrors, detail, engine, effectiveKind, idBase]);
  const polygonPoints = useMemo(() => {
    if (kind !== 'airspace') return [];
    const parts = polygon.split(';').map(part => part.trim()).filter(Boolean);
    const points = parts.map(part => {
      const pair = part.split(',').map(num);
      return pair.length === 2 && pair.every(value => value !== undefined) ? pair as number[] : undefined;
    });
    if (!points.every(Boolean) || points.length < 3) return undefined;
    const vertices = points as number[][];
    const area = vertices.reduce((total, a, index) => { const b = vertices[(index + 1) % vertices.length]; return total + a[0] * b[1] - b[0] * a[1]; }, 0);
    return new Set(vertices.map(point => JSON.stringify(point))).size >= 3 && Math.abs(area) > 1e-9 ? vertices : undefined;
  }, [kind, polygon]);
  const airspaceBad = kind === 'airspace' && (!polygonPoints || num(floor) === undefined || num(ceiling) === undefined || Number(floor) >= Number(ceiling));
  const canCreate = !!chosen && !!detail && detail.id === chosen.id && !detail.abstract && !chosen.abstract && !isLowaltType(chosen) && !unsupportedRegion
    && (!contractKind || kind === contractKind) && (kind !== 'airspace' || engine === 'workflow')
    && !creating && !countInvalid && !idsBad && !posInvalid && !spacingInvalid && !airspaceBad && !Object.keys(factErrors).length;

  const create = async () => {
    if (!canCreate || !chosen) return;
    setCreating(true); setResult(undefined);
    try {
      // Save the local draft first and adopt the server's returned state (T1-007); `/place` takes no
      // revision, so the batch stops at the first failure instead of replaying against a stale draft.
      const saved = saveDraft ? await saveDraft() : await api.save(project.id, draft, project.revision);
      if (!saved) throw Error(L('Save the draft successfully before creating objects.', '请先成功保存草稿，再创建对象。'));
      if (!saveDraft) onProject(saved);
      const created: string[] = [], failed: Array<{ id: string; error: string }> = [];
      for (const row of reviewRows) {
        if (failed.length) break; // keep the server draft authoritative; do not chain stale placements
        const body: Record<string, unknown> = { id: row.id, type: row.type, kind: row.kind, position: row.position, engine: row.engine };
        if (Object.keys(row.facts).length) body.facts = row.facts;
        if (row.kind === 'airspace') { body.polygon = polygonPoints; body.floor_m = Number(floor); body.ceiling_m = Number(ceiling); }
        try {
          const updated = await api.request<Project>(`/v1/studio/workspaces/${encodeURIComponent(project.id)}/place`, { body });
          created.push(row.id); onProject(updated);
        } catch (problem) { failed.push({ id: row.id, error: problem instanceof ApiError ? problem.detail : String(problem) }); }
      }
      setResult({ created, failed });
    } catch (problem) {
      setResult({ created: [], failed: [{ id: '—', error: problem instanceof ApiError ? problem.detail : String(problem) }] });
    } finally { setCreating(false); }
  };

  return <div className="wb-object-form">
    <h4>{L('Add objects', '添加对象')}</h4>
    <p className="hint">{L('Choose a contract type and set the object’s identity, position and initial state.', '选择契约类型，设置对象的身份、位置与初始状态。')}</p>
    {catalogError && <p role="alert" style={{ color: 'var(--wb-bad)' }}>{L('The type catalog is unavailable: ', '类型目录不可用：')}{catalogError}</p>}
    {!catalog && !catalogError && <p className="hint" role="status">{L('Loading the workspace type catalog…', '正在加载工作区类型目录…')}</p>}
    {catalog && <>
      <label className="hint" htmlFor="wb-add-type-q">{L('Search the workspace type catalog', '搜索工作区类型目录')}</label>
      <input id="wb-add-type-q" className="wb-search" value={typeQuery} onChange={event => setTypeQuery(event.target.value)} placeholder={L('e.g. UAV, restaurant, charging', '例如 UAV、餐厅、充电')} />
      <div className="wb-cat-list" role="listbox" aria-label={L('Catalog types', '目录类型')}>
        {matches.map(row => <div key={row.id}>
          <button role="option" aria-selected={row.id === typeId} aria-pressed={row.id === typeId} disabled={row.abstract || isLowaltType(row) || row.id === typeId && unsupportedRegion}
          title={row.abstract ? abstractReason : isLowaltType(row) ? lowaltReason : row.id === typeId && unsupportedRegion ? regionReason : undefined} className="wb-cat-row" onClick={() => chooseType(row)}>
          <b className="wb-mono">{row.id}</b>
          {row.abstract && <span className="wb-tag warn">{L('abstract — cannot be instantiated', '抽象 — 不能实例化')}</span>}
          {!!row.parents.length && <span className="wb-tag wb-mono">{row.parents[0]}</span>}
          </button>
          {isLowaltType(row) && <p className="hint">{lowaltReason}</p>}
          {row.id === typeId && unsupportedRegion && <p className="hint">{regionReason}</p>}
        </div>)}
        {!matches.length && <div className="wb-empty">{L('No catalog type matches.', '没有匹配的目录类型。')}</div>}
      </div>
      {chosen?.abstract && <p className="wb-add-note" style={{ color: 'var(--wb-warn)' }}>{abstractReason}</p>}
    </>}
    {chosen && !chosen.abstract && !isLowaltType(chosen) && <>
      <div className="wb-add-grid">
        <label className="wb-field"><span>{L('Kind', '种类')}</span>
          <select value={kind} disabled={!!contractKind || chosen.id === STUDIO_AIRSPACE} onChange={event => { const next = event.target.value as Kind; setKind(next); setEngine(next === 'entity' ? 'kinematic' : 'workflow'); }}>
            <option value="entity">{L('entity — a moving or active instance', 'entity — 会运动或主动的实例')}</option>
            <option value="facility">{L('facility — a static place (catalog placement)', 'facility — 静态地点（目录放置）')}</option>
            <option value="airspace" disabled={!regionField}>{L('Airspace — an area with floor and ceiling', '空域 — 有下限和上限的区域')}</option>
          </select>
        </label>
        <label className="wb-field"><span>{L('Engine', '引擎')}</span>
          <select value={engine} disabled={kind === 'airspace' || !!chosen.engine} onChange={event => setEngine(event.target.value as Engine)}>
            <option value="kinematic">{L('kinematic — the motion engine moves it', 'kinematic — 运动引擎驱动其移动')}</option>
            <option value="workflow">{L('workflow — a static or service instance', 'workflow — 静态或服务型实例')}</option>
          </select>
        </label>
        <label className="wb-field"><span>{L('id (or id prefix)', 'id（或 id 前缀）')}</span>
          <input className="wb-mono" value={idText} onChange={event => setIdText(event.target.value)} placeholder="uav-1" aria-invalid={!!idBase && (!ID_PATTERN.test(idBase) || !!firstIdClash)} />
        </label>
        <label className="wb-field"><span>{L('count', '数量')}</span>
          <input inputMode="numeric" value={countText} disabled={kind === 'airspace'} onChange={event => setCountText(event.target.value)} />
        </label>
      </div>
      <p className="wb-add-note">{ID_RULE(L)}{count > 1 ? L(' · batch ids are prefix-1 … prefix-N', ' · 批量 id 为 前缀-1 … 前缀-N') : ''}{firstIdClash ? ` — ${L(`an instance already uses ${firstIdClash}`, `已有实例使用 ${firstIdClash}`)}` : ''}</p>
      {kind !== 'airspace' && <div className="wb-add-grid">
        <label className="wb-field"><span>{L('east (m)', '东向 (m)')}</span><input inputMode="decimal" value={east} onChange={event => setEast(event.target.value)} /></label>
        <label className="wb-field"><span>{L('north (m)', '北向 (m)')}</span><input inputMode="decimal" value={north} onChange={event => setNorth(event.target.value)} /></label>
        <label className="wb-field"><span>{L('up (m)', '天向 (m)')}</span><input inputMode="decimal" value={up} onChange={event => setUp(event.target.value)} /></label>
        {count > 1 && <label className="wb-field"><span>{L('spacing along east (m)', '沿东向间距 (m)')}</span><input inputMode="decimal" value={spacing} onChange={event => setSpacing(event.target.value)} /></label>}
        <div className="wb-field" style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button type="button" className="wb-btn small" aria-expanded={picking} aria-pressed={picking} disabled={!canPick}
            title={canPick ? undefined : L('Apply a region first (Scene & space › Region)', '请先应用区域（场景与空间 › 区域）')}
            onClick={() => setPicking(previous => !previous)}>
            {picking ? L('Close pick on map', '关闭地图选点') : L('Pick on map', '地图选点')}
          </button>
        </div>
      </div>}
      {/* TODO R2-MAP: replace this RegionMap mount with the exported PlacePickerMap, using the same marker/onPick values. */}
      {picking && kind !== 'airspace' && <RegionMap
        L={L}
        height={260}
        basemap={mapBasemap}
        coverage={region?.region?.bounds ?? null}
        geometry={mapGeometry}
        pick={{
          onPick: (east, north) => {
            setPickMarker([east, north]);
            setEast((Math.round(east * 10) / 10).toString());
            setNorth((Math.round(north * 10) / 10).toString());
          },
          marker: pickMarker,
        }}
        outsideWarning={L('Outside the applied region — allowed, but the point sits outside the compiled area.', '在已应用区域之外——允许，但该点位于已编译范围外。')}
      />}
      {kind !== 'airspace' && <p className="wb-add-note">{L('Position is measured in metres east, north and up from the workspace origin.', '位置以相对工作区原点的东、北、天方向米数表示。')}</p>}
      {kind === 'airspace' && regionField && <>
        <p className="hint">{L('Set the polygon and altitude limits declared by this contract.', '设置此契约声明的多边形与高度范围。')}</p>
        <div className="wb-add-grid">
          <label className="wb-field"><span>{L('polygon (east,north; …, ≥3 points)', '多边形 (东,北; …，至少 3 点)')}</span><input value={polygon} onChange={event => setPolygon(event.target.value)} placeholder="0,0; 40,0; 40,30" aria-invalid={airspaceBad && !polygonPoints} /></label>
          <label className="wb-field"><span>{L('Floor (m)', '下限 (m)')}</span><input inputMode="decimal" value={floor} onChange={event => setFloor(event.target.value)} /></label>
          <label className="wb-field"><span>{L('Ceiling (m)', '上限 (m)')}</span><input inputMode="decimal" value={ceiling} onChange={event => setCeiling(event.target.value)} /></label>
        </div>
        {polygon.trim() !== '' && airspaceBad && <p className="wb-field-error" role="alert">{L('Enter a polygon with at least three distinct vertices and nonzero area, and a floor below the ceiling.', '请输入至少三个不同顶点且面积非零的多边形，下限必须低于上限。')}</p>}
      </>}
      <h4 style={{ margin: '10px 0 4px' }}>{L('Initial state (modeling assumptions)', '初始状态（建模假设）')}</h4>
      <p className="wb-add-note">{engine === 'kinematic'
        ? L('Values left blank use the server placement defaults, shown as hints; they change real run behaviour.', '留空的值会使用服务器放置默认值（见提示）；它们会改变真实运行行为。')
        : L('Values left blank are omitted; the workflow engine defaults the state to "placed" only if you omit it.', '留空的值不会写入；若省略状态，工作流引擎默认为 "placed"。')}</p>
      {detailError && <p role="alert" style={{ color: 'var(--wb-warn)' }}>{L('Field contract unavailable: ', '字段契约不可用：')}{detailError}</p>}
      {!detail && !detailError && <p className="hint" role="status">{L('Loading the field contract…', '正在加载字段契约…')}</p>}
      {detail && <div className="wb-add-grid">
        {fieldRows.map(field => {
          const fallback = defaultFor(field);
          const schema = field.schema ?? {};
          const placeholder = fallback !== undefined ? `${fallback}${field.metadata?.unit ? ` ${field.metadata.unit}` : ''}` : schema.type === 'boolean' ? L('true or false', 'true 或 false') : schema.type === 'vector' ? L('numbers, comma separated', '数字，逗号分隔') : '';
          return <label className="wb-field" key={field.id}><span className="wb-mono" style={{ fontSize: 11 }}>{field.id}</span>
            <input aria-label={field.id} value={facts[field.id] ?? ''} placeholder={placeholder} aria-invalid={!!factErrors[field.id]}
              onChange={event => setFacts(previous => ({ ...previous, [field.id]: event.target.value }))} />
            {fallback !== undefined && <small style={{ color: 'var(--wb-muted)' }}>{L('server default', '服务器默认值')}{field.metadata?.unit ? ` · ${field.metadata.unit}` : ''}</small>}
            {factErrors[field.id] && <span className="wb-field-error" role="alert">{L('Expected: ', '应为：')}{factErrorText(factErrors[field.id], L)}</span>}
          </label>;
        })}
        {!fieldRows.length && <p className="hint">{L('This type declares no extra contract fields.', '该类型没有额外的契约字段。')}</p>}
      </div>}
      {!!Object.keys(factErrors).length && <p className="wb-field-error" role="alert">{L('Fix the marked contract fields — invalid values are not sent.', '请修正标记的契约字段——无效值不会被发送。')}</p>}
      {facilityLike && <p className="wb-add-note">{L('A facility is a catalog placement: its real-world service behaviour is only as real as the type contract declares.', '设施是一次目录放置：其真实服务行为取决于该类型契约所声明的内容。')}</p>}
      <h4 style={{ margin: '10px 0 4px' }}>{L(`Review — ${reviewRows.length} instance(s) to create`, `确认 — 将创建 ${reviewRows.length} 个实例`)}</h4>
      <table className="wb-table wb-review"><thead><tr><th>{L('id', 'id')}</th><th>{L('type', '类型')}</th><th>{L('kind / engine', '种类 / 引擎')}</th><th>{L('position ENU (m)', '位置 ENU (m)')}</th><th>{L('facts', '事实')}</th></tr></thead><tbody>
        {reviewRows.map(row => <tr key={row.id}><td className="wb-mono">{row.id}</td><td className="wb-mono">{row.type}</td><td>{row.kind} / {row.engine}</td>
          <td className="wb-mono">{row.kind === 'airspace' ? L('Polygon and altitude limits', '多边形与高度范围') : row.position.map(value => String(Math.round(value * 1000) / 1000)).join(', ')}</td>
          <td className="wb-mono" style={{ fontSize: 11 }}>{JSON.stringify(row.facts)}</td></tr>)}
      </tbody></table>
      {result && <div role={result.failed.length ? 'alert' : 'status'} style={{ margin: '6px 0' }}>
        {result.created.length > 0 && <p style={{ color: 'var(--wb-ok)', margin: '2px 0' }}>{L(`Created: ${result.created.join(', ')}`, `已创建：${result.created.join('、')}`)}</p>}
        {result.failed.map(item => <p key={item.id} style={{ color: 'var(--wb-bad)', margin: '2px 0' }}>{L(`Failed on ${item.id}: `, `${item.id} 失败：`)}{operationText(item.error, L)}</p>)}
        {!result.failed.length && <p style={{ color: 'var(--wb-ok)', margin: 0 }}>{L('All instances were placed. Reload shows them below.', '全部实例已放置，下方列表即为结果。')}</p>}
      </div>}
      <div style={{ display: 'flex', gap: 8, margin: '8px 0 2px' }}>
        <button className="wb-btn primary" disabled={!canCreate} onClick={() => void create()}>{creating ? L('Creating…', '创建中…') : L(`Create ${reviewRows.length} objects`, `创建 ${reviewRows.length} 个对象`)}</button>
        <button className="wb-btn ghost" onClick={onClose}>{L('Close', '关闭')}</button>
      </div>
      {!canCreate && !creating && <p className="wb-add-note">{L('Choose a concrete type, a valid unique id and finite positions to enable creation.', '选择具体类型、有效的唯一 id 和有限位置后才能创建。')}</p>}
    </>}
  </div>;
}
