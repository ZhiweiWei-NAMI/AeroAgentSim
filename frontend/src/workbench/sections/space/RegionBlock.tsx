import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError } from '../../api';
import type { SectionProps } from '../types';
import { capabilities } from '../operations';
import {
  applyRegion, cityConflictIssue, generateNetwork, hasCity, heightSummary,
  previewRegion, readRegion, validateRegion,
  type ExtractInfo, type NetworkResult, type RegionDraft, type RegionPreview,
} from './model';
import { RegionMap, enuLines, enuRings, type RegionMapBasemap, type RegionMapGeometry, type RegionMapInstance } from './RegionMap';
import { paletteColorFor } from '../objects/RegionMapSupport';

export { originShiftM } from './model';

/** Number formatter for review figures. */
export const m = (value: number, digits = 1) => `${value.toFixed(digits)} m`;
export const coord = (value: number) => value.toFixed(6).replace(/\.?0+$/, '');

// Visible, editable modelling defaults: the form never starts with required fields empty.
const emptyDraft = (): RegionDraft => ({ extract: '', west: '', south: '', east: '', north: '', alt: '0', level: '3.5' });

/** Serialise the four bounds + alt + level + extract; any change to these invalidates the preview (design ②). */
const previewKey = (form: RegionDraft) => [form.extract, form.west, form.south, form.east, form.north, form.alt, form.level].join('|');

/** Spec 4: chip when the compiled geometry set was truncated server-side. */
const MAP_BUILDING_LIMIT = 3000;

/**
 * Region stage: pick a configured local OSM extract, confirm WGS84 bounds and modelling
 * assumptions, review server-side impact, then apply. Only configured local extracts are
 * offered — there is no online search (see tools/build_map.py to add one).
 */
export function RegionBlock({ project, api, draft, edit, issues, onProject, saveDraft, L }: SectionProps) {
  const fieldId = useId();
  const [extracts, setExtracts] = useState<ExtractInfo[]>(), [catalogError, setCatalogError] = useState<string>();
  const [form, setForm] = useState<RegionDraft>(emptyDraft);
  const [preview, setPreview] = useState<RegionPreview['preview']>(), [previewError, setPreviewError] = useState<string>(), [previewing, setPreviewing] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false), [applying, setApplying] = useState(false), [applyError, setApplyError] = useState<string>();
  const [netBusy, setNetBusy] = useState(false), [netError, setNetError] = useState<string>(), [netResult, setNetResult] = useState<NetworkResult>();
  const compiled = useMemo(() => readRegion(project as never), [project]);
  const caps = capabilities(project as never);
  const networkCapable = caps ? caps.region : !!compiled; // C13: the server decides; no region on the server → inline reason.
  const conflict = cityConflictIssue(issues) ?? (hasCity(draft) && compiled ? { message: L('A fixed city and a compiled region are both configured; the run would otherwise mix city sources.', '同时配置了固定城市与已编译区域，否则运行会混用城市数据源。') } : undefined);
  const touchedExtract = useRef(false);

  // --- Region map state (C9 basemap, C10 preview geometry, compiled scene, instances) ---
  const [basemap, setBasemap] = useState<RegionMapBasemap | null>(null);
  const [mapGeometry, setMapGeometry] = useState<RegionMapGeometry | null>(null);
  const [mapTruncated, setMapTruncated] = useState(false);
  const [mapInstances, setMapInstances] = useState<RegionMapInstance[]>([]);

  const catalogRequest = useCallback(async (signal: AbortSignal) => {
    const catalog = await api.request<{ extracts?: ExtractInfo[]; basemap?: { url?: string; attribution?: string } }>('/v1/studio/catalog', { signal });
    const row = catalog?.extracts?.find(item => item.id === form.extract) ?? undefined;
    const tile = row?.basemap ?? catalog?.basemap;
    return {
      extracts: Array.isArray(catalog?.extracts) ? catalog.extracts : [],
      basemap: tile?.url ? { url: tile.url, attribution: tile.attribution ?? '© OpenStreetMap contributors' } : null,
    };
  }, [api, form.extract]);

  // One shared catalog read per extract selection: extract list + C9 basemap (no duplicate request).
  useEffect(() => {
    const signal = new AbortController();
    setCatalogError(undefined);
    catalogRequest(signal.signal)
      .then(({ extracts: list, basemap: tile }) => { setExtracts(list); setBasemap(tile); })
      .catch(problem => {
        if (signal.signal.aborted) return;
        setExtracts(undefined);
        setBasemap(null);
        setCatalogError(problem instanceof ApiError && problem.unavailable ? L('The studio catalog is unavailable on this server, so no extract can be listed here.', '此服务器没有提供 studio 目录，无法列出可用的 OSM 提取数据。') : String(problem));
      });
    return () => signal.abort();
  }, [catalogRequest, L]);

  // C10 preview geometry wins while reviewing; otherwise the applied region's compiled scene.
  useEffect(() => {
    if (preview) {
      // The preview answer carries counts only; the compiled map shows once applied. For the
      // review state the map shows the selected bounds plus the preview origin crosshair.
      setMapGeometry({ origin: preview.origin });
      setMapTruncated(false);
      return;
    }
    if (!compiled?.scene?.origin) { setMapGeometry(null); setMapTruncated(false); setMapInstances([]); return; }
    const origin = compiled.scene.origin;
    setMapGeometry({
      origin,
      buildings: enuRings(compiled.scene.geojson?.features ?? []),
      roads: enuLines((compiled.scene.roads ?? []).filter(Array.isArray) as number[][][]),
    });
    setMapTruncated((compiled.scene.geojson?.features?.length ?? 0) > MAP_BUILDING_LIMIT);
    // Existing instances with ENU positions (aas.studio.position_enu_m or the presentation position field).
    const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
    const presentation = (Array.isArray(draft.presentation) ? draft.presentation : []) as Array<Record<string, any>>;
    const typeOrder = new Map<string, number>();
    for (const entity of entities) if (!typeOrder.has(entity.type)) typeOrder.set(entity.type, typeOrder.size);
    const positions = presentation.length ? presentation.map(row => row.positionField ?? 'aas.studio.position_enu_m') : [];
    setMapInstances(entities.flatMap(entity => {
      const field = positions.find(candidate => entity.facts?.[candidate]) ?? 'aas.studio.position_enu_m';
      const pos = entity.facts?.[field];
      if (!Array.isArray(pos) || pos.length !== 3 || !pos.every(Number.isFinite)) return [];
      return [{ east: pos[0] as number, north: pos[1] as number, color: paletteColorFor(entity.type, typeOrder), label: entity.label ? `${entity.label} · ${entity.id}` : String(entity.id ?? '') }];
    }));
  }, [preview, compiled, draft.entities, draft.presentation]);

  // Prefill from the extract's known bounds once, on selection.
  useEffect(() => {
    if (!touchedExtract.current || !form.extract || !extracts) return;
    touchedExtract.current = false;
    const found = extracts.find(item => item.id === form.extract);
    if (found?.bounds?.length === 4) setForm(previous => ({ ...previous, west: String(found.bounds![0]), south: String(found.bounds![1]), east: String(found.bounds![2]), north: String(found.bounds![3]) }));
  }, [form.extract, extracts]);

  const extractBounds = extracts?.find(item => item.id === form.extract)?.bounds;
  const [edited, setEdited] = useState<Partial<Record<keyof RegionDraft, boolean>>>({});
  const { errors, body } = validateRegion(form, extractBounds);

  // Two-way sync: the map's selected rectangle ↔ the four W/S/E/N inputs (6 decimals on write).
  const formBounds = useMemo(() => {
    const w = Number(form.west), s = Number(form.south), e = Number(form.east), n = Number(form.north);
    return [form.west, form.south, form.east, form.north].every(raw => raw.trim() !== '' && Number.isFinite(Number(raw)))
      ? [w, s, e, n] : null;
  }, [form.west, form.south, form.east, form.north]);
  const [mapBounds, setMapBounds] = useState<number[] | null>(formBounds);
  const boundsSource = useRef<'form' | 'map'>('form');
  useEffect(() => { if (boundsSource.current === 'form') setMapBounds(formBounds); }, [formBounds]);
  const onMapBounds = (next: number[]) => {
    boundsSource.current = 'map';
    setMapBounds(next);
    setForm(previous => ({ ...previous, west: next[0].toFixed(6), south: next[1].toFixed(6), east: next[2].toFixed(6), north: next[3].toFixed(6) }));
    setEdited(previous => ({ ...previous, west: true, south: true, east: true, north: true }));
  };
  useEffect(() => { boundsSource.current = 'form'; }, [form.west, form.south, form.east, form.north]);

  // Errors only once the user has actually edited that field (bounds errors likewise after edit).
  const shownErrors = Object.fromEntries(Object.entries(errors).filter(([key]) => edited[key as keyof RegionDraft])) as typeof errors;
  const invalid = Object.keys(errors).length > 0 || !body;
  const setField = (key: keyof RegionDraft) => (value: string) => { setEdited(previous => ({ ...previous, [key]: true })); setForm(previous => ({ ...previous, [key]: value })); };

  const needsConfirm = !!preview && (preview.origin_shift_m === null || preview.origin_shift_m > 0 || preview.affected_entities.length > 0 || preview.network_invalidated);
  // Design ②: the preview is tied to the exact inputs it was made from; any change marks it out of date.
  const [previewedFrom, setPreviewedFrom] = useState('');
  const currentKey = previewKey(form);
  const stale = !!preview && previewedFrom !== currentKey;
  const doPreview = async () => {
    if (!body) return;
    setPreviewing(true); setPreviewError(undefined); setApplyError(undefined); setAcknowledged(false);
    try {
      const answer = await previewRegion(api, project.id, body);
      setPreview(answer);
      setPreviewedFrom(previewKey(form));
    }
    catch (problem) { setPreview(undefined); setPreviewError(problem instanceof ApiError && problem.unavailable ? L('This server does not support region preview (dry run). The region was not applied.', '服务器不支持区域预演（dry run）。区域未应用。') : String(problem)); }
    finally { setPreviewing(false); }
  };
  const doApply = async () => {
    if (!body || !preview || stale || (needsConfirm && !acknowledged)) return;
    setApplying(true); setApplyError(undefined);
    try {
      const saved = await saveDraft();
      if (!saved) throw new Error(L('The draft could not be saved, so the region was not applied.', '草稿保存失败，区域未应用。'));
      await applyRegion(api, saved.id, body);
      onProject(await api.project(saved.id)); // re-fetch: the server is the source of truth (design ③)
      setPreview(undefined); setPreviewedFrom(''); setAcknowledged(false);
    } catch (problem) { setApplyError(String(problem)); }
    finally { setApplying(false); }
  };
  const doNetwork = async () => {
    if (!networkCapable) return;
    setNetBusy(true); setNetError(undefined);
    try {
      const saved = await saveDraft();
      if (!saved) throw new Error(L('The draft could not be saved, so the network was not generated.', '草稿保存失败，路网未生成。'));
      try {
        const result = await generateNetwork(api, saved.id);
        setNetResult(result);
        onProject(await api.project(saved.id)); // re-fetch: network state comes from the server (design ④)
      } catch (problem) {
        if (problem instanceof ApiError && problem.unavailable) throw new Error(L('This server does not expose the road-network endpoint.', '此服务器未提供路网生成接口。'));
        throw problem;
      }
    } catch (problem) { setNetError(String(problem instanceof Error ? problem.message : problem)); }
    finally { setNetBusy(false); }
  };

  const network = compiled?.network ?? netResult;
  // Design ④: state comes from the server. The workspace record's `network_state`
  // ("none" | "generated" | "stale") is authoritative when present; only when the server does not
  // send it yet do we fall back to the C13 capability inference (T1-003).
  const serverNetworkState = project.network_state;
  const networkState: 'none' | 'generated' | 'stale' = serverNetworkState === 'none' || serverNetworkState === 'generated' || serverNetworkState === 'stale'
    ? serverNetworkState
    : !network
      ? 'none'
      : caps && !caps.network ? 'stale' : (network.status === 'stale' ? 'stale' : 'generated');
  const heights = compiled ? heightSummary(compiled.scene) : undefined;
  const fieldRow = (key: 'west' | 'south' | 'east' | 'north' | 'alt' | 'level', label: string, hint: string) => <label className="wb-region-field">
    <span>{label}</span>
    <input className="wb-search" inputMode="decimal" value={form[key]} aria-label={label} aria-invalid={shownErrors[key] ? true : undefined}
      aria-describedby={shownErrors[key] ? `${fieldId}-${key}-error` : undefined} placeholder={hint}
      onChange={event => setField(key)(event.target.value)} />
    {shownErrors[key] && <span id={`${fieldId}-${key}-error`} className="wb-field-error" role="alert">{shownErrors[key]}</span>}
  </label>;

  return <section className="wb-region" aria-label={L('Region', '区域')}>
    <h4>{L('Region', '区域')}</h4>
    <p className="hint">{L(
      'Only server-configured local OSM extracts are supported — there is no online place search. Ask an operator to add one with tools/build_map.py.',
      '仅支持服务器已配置的本地 OSM 提取数据——没有在线地点搜索。可用 tools/build_map.py 添加新的提取数据。')}</p>

    {compiled && <div className="wb-region-card" data-testid="region-summary">
      <h4>{L('Compiled region', '已编译区域')} <span className="wb-tag ok">{L('from server', '来自服务器')}</span></h4>
      <dl className="wb-region-meta">
        <dt>{L('Source', '数据源')}</dt><dd className="wb-mono">{compiled.region.extract}{compiled.scene?.attribution ? ` · ${compiled.scene.attribution}` : ''}</dd>
        <dt>{L('Bounds (WGS84)', '范围（WGS84）')}</dt><dd className="wb-mono">{compiled.region.bounds.map(coord).join(', ')}</dd>
        {compiled.scene?.origin && <><dt>{L('ENU origin', 'ENU 原点')}</dt><dd className="wb-mono">{coord(compiled.scene.origin.lat)}, {coord(compiled.scene.origin.lon)} · alt {m(compiled.scene.origin.alt, 0)}</dd></>}
        {compiled.scene?.ground && <><dt>{L('Ground extent', '地面范围')}</dt><dd className="wb-mono">{m(compiled.scene.ground.width_m)} × {m(compiled.scene.ground.depth_m)}</dd></>}
        {compiled.scene?.roads && <><dt>{L('Roads', '道路')}</dt><dd>{compiled.scene.roads.length} <span className="wb-tag">ENU polyline</span></dd></>}
      </dl>
      {heights && <p className="hint">{L(
        `Heights: ${heights.sourced} from OSM height, ${heights.estimated} estimated from building:levels × level height, ${heights.missing} kept as 2D footprints without a volume (OSM often lacks heights; none receive a silent default).`,
        `高度：${heights.sourced} 个来自 OSM height，${heights.estimated} 个按 building:levels × 层高估算，${heights.missing} 个仅保留二维轮廓无体量（OSM 常缺高度；绝不使用静默默认高度）。`)}</p>}
      {!!compiled.scene?.diagnostics?.length && <ul className="wb-region-diag">{compiled.scene.diagnostics.map((line, index) => <li key={index} className="wb-mono">{line}</li>)}</ul>}
    </div>}

    <div className="wb-region-card" data-testid="network-summary">
      <h4>{L('Road network', '路网')} <span className="wb-tag info">{networkState === 'none' ? L('Not generated', '未生成') : networkState === 'stale' ? L('stale', '已失效') : networkState}</span></h4>
      {network && <dl className="wb-region-meta">
        {typeof network.edge_count === 'number' && <><dt>{L('Edges', '路段')}</dt><dd className="wb-mono">{network.edge_count}</dd></>}
        {typeof network.lane_count === 'number' && <><dt>{L('Lanes', '车道')}</dt><dd className="wb-mono">{network.lane_count}</dd></>}
        {network.location && <><dt>{L('Projection', '投影')}</dt><dd className="wb-mono">{JSON.stringify(network.location)}</dd></>}
      </dl>}
      {networkState === 'stale' && <p className="wb-region-warn" role="status">⚠ {L(
        'Out of date — the region changed; generate again.',
        '已失效——区域已变更，请重新生成。')}</p>}
      <p className="hint">{L('This network is generated geometry for the traffic engine, not a traffic run. Generating it changes nothing else in the draft.', '此路网是为交通引擎生成的几何，不是一次交通仿真运行。生成它不会改动草稿的其他内容。')}</p>
    </div>

    <label className="wb-region-field">
      <span>{L('Extract', 'OSM 提取数据')}</span>
      <select className="wb-search" value={form.extract} aria-label={L('Extract', 'OSM 提取数据')}
        onChange={event => { touchedExtract.current = true; setField('extract')(event.target.value); }}>
        <option value="">{L('Select a configured extract…', '选择已配置的提取数据…')}</option>
        {(extracts ?? []).map(item => <option key={item.id} value={item.id}>{item.label ?? item.id}{item.bounds ? L(' (coverage known)', '（已知覆盖范围）') : ''}</option>)}
      </select>
      {catalogError && <span className="wb-field-error" role="alert">{catalogError}</span>}
      {!extracts && !catalogError && <span className="hint">{L('Loading the extract catalog…', '正在加载提取数据目录…')}</span>}
    </label>

    <RegionMap
      L={L}
      basemap={basemap}
      coverage={extractBounds ?? null}
      bounds={mapBounds}
      onBoundsChange={onMapBounds}
      geometry={mapGeometry}
      instances={mapInstances}
      outsideWarning={L('Outside the extract coverage — allowed, but the server will clamp the region.', '超出提取数据覆盖范围——允许，但服务器会进行收敛。')}
    />
    {mapTruncated && <span className="wb-map-chip warn" role="status">{L('Showing the first 3000 buildings', '仅显示前 3000 个建筑物')}</span>}

    <div className="wb-region-grid">
      {fieldRow('west', L('West (°)', '西经（°）'), '121.50')}
      {fieldRow('south', L('South (°)', '南纬（°）'), '31.30')}
      {fieldRow('east', L('East (°)', '东经（°）'), '121.52')}
      {fieldRow('north', L('North (°)', '北纬（°）'), '31.31')}
      {fieldRow('alt', L('Ground altitude (m)', '地面海拔（m）'), '0')}
      {fieldRow('level', L('Level height (m)', '层高（m）'), '3.5')}
    </div>
    <p className="hint">{L('Defaults are modelling assumptions — edit them for your site.', '默认值是建模假设——请按你的场地修改。')}</p>

    <div className="wb-region-actions">
      <button className="wb-btn" disabled={invalid || previewing} onClick={() => void doPreview()}>{previewing ? L('Previewing…', '预演中…') : L('Preview region', '预览区域')}</button>
      <button className="wb-btn primary" data-testid="apply-region" disabled={invalid || !preview || stale || applying || (needsConfirm && !acknowledged)}
        onClick={() => void doApply()}>{applying ? L('Applying…', '应用中…') : L('Apply region', '应用区域')}</button>
      {stale && <span className="wb-region-warn" role="status">{L('Out of date — preview again.', '已失效——请重新预览。')}</span>}
      {!networkCapable && <span className="wb-region-warn" role="status">{L('Apply a region first (step ③).', '请先应用区域（步骤 ③）。')}</span>}
      <button className="wb-btn" data-testid="generate-network" disabled={!networkCapable || netBusy}
        title={networkCapable ? undefined : L('Apply a region first (step ③).', '请先应用区域（步骤 ③）。')}
        onClick={() => void doNetwork()}>{netBusy ? L('Generating…', '生成中…') : L('Generate road network', '生成路网')}</button>
    </div>
    {invalid && <p className="hint">{L('Enter valid bounds, altitude and level height to enable the actions above.', '输入有效的范围、海拔与层高后，上方操作才会启用。')}</p>}
    {previewError && <p className="wb-field-error" role="alert">{previewError}</p>}
    {applyError && <p className="wb-field-error" role="alert">{applyError}</p>}
    {netError && <p className="wb-field-error" role="alert">{netError}</p>}

    {preview && <div className="wb-region-card" data-testid="region-review" role="group" aria-label={L('Region review', '区域评审')} aria-disabled={stale || undefined}
      style={stale ? { opacity: 0.45 } : undefined}>
      <h4>{L('Review before applying', '应用前评审')}{stale && <span className="wb-tag warn">{L('Out of date — preview again', '已失效——请重新预览')}</span>}</h4>
      <dl className="wb-region-meta">
        <dt>{L('Extract', '提取数据')}</dt><dd className="wb-mono">{preview.extract}</dd>
        <dt>{L('Bounds (WGS84)', '范围（WGS84）')}</dt><dd className="wb-mono">{preview.bounds.map(coord).join(', ')}</dd>
        <dt>{L('ENU origin', 'ENU 原点')}</dt><dd className="wb-mono">{coord(preview.origin.lat)}, {coord(preview.origin.lon)} · alt {m(preview.origin.alt, 0)}</dd>
        <dt>{L('Buildings', '建筑物')}</dt><dd className="wb-mono">{preview.buildings}</dd>
        <dt>{L('Roads', '道路')}</dt><dd className="wb-mono">{preview.roads}</dd>
        <dt>{L('Attribution', '来源署名')}</dt><dd>{preview.attribution || L('unknown', '未知')}</dd>
        <dt>{L('Height source', '高度来源')}</dt><dd className="wb-mono">{preview.height_source || L('unknown', '未知')}</dd>
      </dl>
      {!!preview.previous_origin && <p className="hint">{L(
        `Origin moves ${preview.origin_shift_m == null ? L('by an unknown distance', '未知距离') : m(preview.origin_shift_m)}; affected objects ${preview.affected_entities.length ? preview.affected_entities.join(', ') : L('none', '无')} keep their ENU coordinates, so their geographic position changes with the origin.`,
        `原点移动 ${preview.origin_shift_m == null ? '未知距离' : m(preview.origin_shift_m)}；受影响对象 ${preview.affected_entities.length ? preview.affected_entities.join('、') : '无'} 的 ENU 坐标不变，其地理位置随原点改变。`)}</p>}
      {preview.network_invalidated && <p className="wb-region-warn" role="status">⚠ {L('Applying replaces the region, so the previously generated road network becomes stale and must be regenerated.', '应用会替换区域，已生成的路网将失效，需要重新生成。')}</p>}
      {preview.city_conflict && <p className="wb-region-warn" role="status">⚠ {L('A fixed city is also configured. The run must use one source; clear the city below or skip the region.', '同时配置了固定城市。运行只能使用一个数据源：请在下方清除城市，或放弃该区域。')}</p>}
      {!!preview.diagnostics.length && <ul className="wb-region-diag">{preview.diagnostics.map((line, index) => <li key={index} className="wb-mono">{line}</li>)}</ul>}
      {needsConfirm && <label className="wb-region-confirm">
        <input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />
        {L('I understand the origin shift, affected objects and stale road network described above.', '我已理解上述原点位移、受影响对象与失效路网。')}
      </label>}
    </div>}

    {(conflict || hasCity(draft)) && compiled && <div className="wb-region-card" data-testid="city-conflict">
      <p className="wb-region-warn" role="status">⚠ {conflict?.message ?? L(
        'The fixed city and the compiled region both describe geography; the run would otherwise mix sources. Keep only one.',
        '固定城市与已编译区域都在描述地理范围；运行会混用数据源。请只保留一个。')}</p>
      {hasCity(draft) && <button className="wb-btn small" data-testid="clear-city" onClick={() => edit('/city', undefined)}>
        {L('Use the region (clear city)', '使用区域（清除城市）')}</button>}
    </div>}
    {!!cityConflictIssue(issues) && <p className="wb-field-error" role="alert">{cityConflictIssue(issues)!.message}</p>}
  </section>;
}
