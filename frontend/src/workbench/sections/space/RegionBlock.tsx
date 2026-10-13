import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError } from '../../api';
import type { SectionProps } from '../types';
import { capabilities } from '../operations';
import {
  applyRegion, cityConflictIssue, generateNetwork, hasCity, heightSummary,
  catalogBasemap, extractLabel, fetchMapCatalog, previewMapGeometry, previewRegion, readRegion, regionDraft, sceneMapGeometry, validateRegion,
  type MapCatalog, type NetworkResult, type RegionDraft, type RegionPreview,
} from './model';
import { RegionMap } from '../../LazyRegionMap';
import { regionMapObjects } from '../objects/RegionMapSupport';
import { validWgs84Bounds } from '../../geo';

export { originShiftM } from './model';


/** One-line summary of buildings without height data, from the preview geometry. */
function heightSummaryFromPreview(preview: RegionPreview['preview'], L: SectionProps['L']): string | undefined {
  const features = preview.geometry?.buildings.features.filter(feature => feature.geometry?.type === 'Polygon' || feature.geometry?.type === 'MultiPolygon') ?? [];
  const missing = features.filter(feature => typeof feature.properties?.height_source !== 'string').length;
  return missing > 0
    ? L(`${missing} building${missing === 1 ? ' has' : 's have'} no height data — shown as footprints`, `${missing} 栋建筑缺少高度数据，以二维轮廓显示`)
    : undefined;
}

/** Number formatter for review figures. */
export const m = (value: number, digits = 1) => `${value.toFixed(digits)} m`;
export const coord = (value: number) => value.toFixed(6).replace(/\.?0+$/, '');

/** Compare research values, so harmless numeric formatting and rerenders keep the review. */
const previewKey = (form: RegionDraft) => JSON.stringify(validateRegion(form).body ?? form);

/** Older servers return some explanatory fields as "en / zh". Render only one locale. */
const serverText = (value: string, L: SectionProps['L']) => {
  const parts = value.split(' / ');
  return parts.length === 2 ? L(parts[0], parts[1]) : L(value, value);
};
const fieldErrorText = (message: string, L: SectionProps['L']) => {
  const zh: Record<string, string> = {
    'Longitude between -180 and 180 degrees.': '经度须在 -180 至 180 度之间。',
    'Latitude between -90 and 90 degrees.': '纬度须在 -90 至 90 度之间。',
    'West must be smaller than east (WGS84 degrees, no antimeridian wrap).': '西侧经度须小于东侧经度（WGS84 度，不跨越日期变更线）。',
    'South must be smaller than north.': '南侧纬度须小于北侧纬度。',
    'Ground altitude in metres is required.': '请填写地面海拔（米）。',
    'Level height must be a positive number of metres.': '层高须为正数（米）。',
    'Bounds must stay inside the extract coverage.': '范围须在提取数据的覆盖区域内。',
  };
  return L(message, zh[message] ?? message);
};

/**
 * Region stage: pick a configured local OSM extract, confirm WGS84 bounds and modelling
 * assumptions, review server-side impact, then apply. Only configured local extracts are
 * offered — there is no online search (see tools/build_map.py to add one).
 */
export function RegionBlock({ project, api, draft, edit, issues, onProject, saveDraft, L }: SectionProps) {
  const fieldId = useId();
  const [catalog, setCatalog] = useState<MapCatalog>(), [catalogError, setCatalogError] = useState<unknown>();
  const compiled = useMemo(() => readRegion(project), [project]);
  const [form, setForm] = useState<RegionDraft>(() => regionDraft(compiled?.region));
  const [edited, setEdited] = useState<Partial<Record<keyof RegionDraft, boolean>>>({});
  const [preview, setPreview] = useState<RegionPreview['preview']>(), [previewError, setPreviewError] = useState<string>(), [previewing, setPreviewing] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false), [applying, setApplying] = useState(false), [applyError, setApplyError] = useState<string>();
  const [netBusy, setNetBusy] = useState(false), [netError, setNetError] = useState<string>(), [netResult, setNetResult] = useState<NetworkResult>();
  const caps = capabilities(project as never);
  const networkCapable = caps ? caps.region : !!compiled; // C13: the server decides; no region on the server → inline reason.
  const conflict = cityConflictIssue(issues) ?? (hasCity(draft) && compiled ? { message: L('A fixed city and a compiled region are both configured; the run would otherwise mix city sources.', '同时配置了固定城市与已编译区域，否则运行会混用城市数据源。') } : undefined);
  const extracts = catalog?.extracts;
  const basemap = catalogBasemap(catalog, form.extract);
  const mapGeometry = useMemo(() => preview ? previewMapGeometry(preview) : sceneMapGeometry(compiled?.scene), [preview, compiled]);
  const mapObjects = useMemo(() => regionMapObjects(draft, L), [draft.entities, draft.presentation, draft.generators, draft.engines, L]);
  const appliedKey = JSON.stringify([project.id, compiled?.region]);
  const hydratedFrom = useRef(appliedKey);
  const defaultSelected = useRef(false);

  // One catalog request supplies every extract's coverage/counts and the shared C9 tiles.
  useEffect(() => {
    const signal = new AbortController();
    setCatalogError(undefined);
    fetchMapCatalog(api, signal.signal)
      .then(answer => { if (!signal.signal.aborted) setCatalog(answer); })
      .catch(problem => {
        if (signal.signal.aborted) return;
        setCatalog(undefined); setCatalogError(problem);
      });
    return () => signal.abort();
  }, [api]);

  // Adopt actual region mutations, while unrelated server refreshes preserve local field edits.
  useEffect(() => {
    if (hydratedFrom.current === appliedKey) return;
    hydratedFrom.current = appliedKey;
    setForm(regionDraft(compiled?.region)); setEdited({});
    setPreview(undefined); setAcknowledged(false); setNetResult(undefined);
  }, [appliedKey, compiled]);

  // For a new project, prefer the richest actual catalog entry. Do not overwrite an edit.
  useEffect(() => {
    if (!extracts || defaultSelected.current) return;
    defaultSelected.current = true;
    if (compiled || Object.keys(edited).length || form.extract) return;
    const richer = extracts.filter(item => Number.isInteger(item.buildings) && item.buildings! > 0 && validWgs84Bounds(item.bounds))
      .sort((a, b) => b.buildings! - a.buildings!)[0];
    if (richer) setForm(previous => ({ ...previous, extract: richer.id, west: String(richer.bounds![0]),
      south: String(richer.bounds![1]), east: String(richer.bounds![2]), north: String(richer.bounds![3]) }));
  }, [extracts, compiled, edited, form.extract]);

  const extractBounds = extracts?.find(item => item.id === form.extract)?.bounds;
  const { errors, body } = validateRegion(form, extractBounds);

  // Two-way sync: the map's selected rectangle ↔ the four W/S/E/N inputs (6 decimals on write).
  const formBounds = useMemo(() => {
    const w = Number(form.west), s = Number(form.south), e = Number(form.east), n = Number(form.north);
    return [form.west, form.south, form.east, form.north].every(raw => raw.trim() !== '' && Number.isFinite(Number(raw)))
      ? [w, s, e, n] : null;
  }, [form.west, form.south, form.east, form.north]);
  const onMapBounds = useCallback((next: number[]) => {
    setForm(previous => ({ ...previous, west: next[0].toFixed(6), south: next[1].toFixed(6), east: next[2].toFixed(6), north: next[3].toFixed(6) }));
    setEdited(previous => ({ ...previous, west: true, south: true, east: true, north: true }));
  }, []);

  // Errors only once the user has actually edited that field (bounds errors likewise after edit).
  const shownErrors = Object.fromEntries(Object.entries(errors).filter(([key]) => edited[key as keyof RegionDraft])) as typeof errors;
  const invalid = Object.keys(errors).length > 0 || !body;
  const setField = (key: keyof RegionDraft) => (value: string) => { setEdited(previous => ({ ...previous, [key]: true })); setForm(previous => ({ ...previous, [key]: value })); };

  const needsConfirm = !!preview && (preview.origin_shift_m === null || preview.origin_shift_m > 0 || preview.affected_entities.length > 0 || preview.network_invalidated);
  // Design ②: the preview is tied to the exact inputs it was made from; any change marks it out of date.
  const [previewedFrom, setPreviewedFrom] = useState('');
  const [previewRevision, setPreviewRevision] = useState<number>();
  const currentKey = previewKey(form);
  const stale = !!preview && (previewedFrom !== currentKey || previewRevision !== project.revision);
  const doPreview = async () => {
    if (!body) return;
    setPreviewing(true); setPreviewError(undefined); setApplyError(undefined); setAcknowledged(false);
    try {
      const answer = await previewRegion(api, project.id, body);
      setPreview(answer);
      setPreviewedFrom(previewKey(form));
      setPreviewRevision(project.revision);
    }
    catch (problem) { setPreview(undefined); setPreviewError(problem instanceof ApiError && problem.unavailable ? L('This server does not support region preview (dry run). The region was not applied.', '服务器不支持区域预演（dry run）。区域未应用。') : String(problem)); }
    finally { setPreviewing(false); }
  };
  const doApply = async () => {
    if (!body || !preview || stale || previewing || applying || (needsConfirm && !acknowledged)) return;
    setApplying(true); setApplyError(undefined);
    try {
      const saved = await saveDraft();
      if (!saved) throw new Error(L('The draft could not be saved, so the region was not applied.', '草稿保存失败，区域未应用。'));
      if (saved.revision !== previewRevision) {
        setPreviewedFrom('');
        throw new Error(L('The draft changed while saving — preview the region again.', '保存时草稿已变更——请重新预览区域。'));
      }
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
    {shownErrors[key] && <span id={`${fieldId}-${key}-error`} className="wb-field-error" role="alert">{fieldErrorText(shownErrors[key]!, L)}</span>}
  </label>;

  return <section className="wb-region" aria-label={L('Region', '区域')}>
    <h4>{L('Region', '区域')}</h4>
    <p className="hint">{L(
      'Choose a map extract, select an area and review its buildings and roads before applying.',
      '选择地图数据和范围，查看建筑与道路后再应用。')}</p>

    {compiled && <div className="wb-region-card" data-testid="region-summary">
      <h4>{L('Applied region', '已应用区域')} <span className="wb-tag ok">{L('saved', '已保存')}</span></h4>
      <dl className="wb-region-meta">
        <dt>{L('Source', '数据源')}</dt><dd className="wb-mono">{compiled.region.extract}{compiled.scene?.attribution ? ` · ${compiled.scene.attribution}` : ''}</dd>
        <dt>{L('Area (W, S, E, N)', '范围（西、南、东、北）')}</dt><dd className="wb-mono">{compiled.region.bounds.map(value => value.toFixed(5)).join(', ')}</dd>
        {compiled.scene?.roads && <><dt>{L('Roads', '道路')}</dt><dd>{compiled.scene.roads.length}</dd></>}
        {compiled.scene?.attribution && <><dt>{L('Attribution', '来源署名')}</dt><dd>{compiled.scene.attribution}</dd></>}
      </dl>
      {(compiled.scene?.origin || compiled.scene?.ground || heights || !!compiled.scene?.diagnostics?.length) && <details className="wb-region-details">
        <summary>{L('Details', '详情')}</summary>
        {compiled.scene?.origin && <dl className="wb-region-meta">
          <dt>{L('Height source', '高度来源')}</dt><dd>{L('per-building OSM heights and estimates', '逐栋 OSM 高度与估算')}</dd>
          {compiled.scene?.ground && <><dt>{L('ENU origin', 'ENU 原点')}</dt><dd className="wb-mono">{coord(compiled.scene.origin.lat)}, {coord(compiled.scene.origin.lon)} · {L('altitude', '海拔')} {m(compiled.scene.origin.alt, 0)}</dd></>}
          {compiled.scene?.ground && <><dt>{L('Ground extent', '地面范围')}</dt><dd className="wb-mono">{m(compiled.scene.ground.width_m)} × {m(compiled.scene.ground.depth_m)}</dd></>}
        </dl>}
        {heights && <p className="hint">{L(
          `Heights: ${heights.sourced} from OSM height, ${heights.estimated} estimated from building:levels × level height, ${heights.missing} kept as 2D footprints without a volume.`,
          `高度：${heights.sourced} 个来自 OSM 高度，${heights.estimated} 个按建筑层数 × 层高估算，${heights.missing} 个保留为二维轮廓。`)}</p>}
        {!!compiled.scene?.diagnostics?.length && <ul className="wb-region-diag">{compiled.scene.diagnostics.map((line, index) => <li key={index} className="wb-mono">{serverText(line, L)}</li>)}</ul>}
      </details>}
    </div>}

    <div className="wb-region-card" data-testid="network-summary">
      <h4>{L('Road network', '路网')} <span className="wb-tag info">{networkState === 'none' ? L('Not generated', '未生成') : networkState === 'stale' ? L('stale', '已失效') : L('generated', '已生成')}</span></h4>
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
        onChange={event => {
          const value = event.target.value;
          if (value === form.extract) return;
          setEdited(previous => ({ ...previous, extract: true }));
          const found = extracts?.find(item => item.id === value);
          const extent = validWgs84Bounds(found?.bounds) ? found.bounds : null;
          setForm(previous => ({ ...previous, extract: value, ...(extent ? { west: String(extent[0]),
            south: String(extent[1]), east: String(extent[2]), north: String(extent[3]) } : {}) }));
        }}>
        <option value="">{L('Select a configured extract…', '选择已配置的提取数据…')}</option>
        {form.extract && !extracts?.some(item => item.id === form.extract) && <option value={form.extract}>{L(form.extract, form.extract)}</option>}
        {(extracts ?? []).map(item => <option key={item.id} value={item.id}>{extractLabel(item, L)}</option>)}
      </select>
      {!!catalogError && <span className="wb-field-error" role="alert">{catalogError instanceof ApiError && catalogError.unavailable
        ? L('The map extracts are unavailable on this server.', '此服务器的地图提取数据不可用。')
        : L(`Could not load map extracts: ${String(catalogError)}`, `无法加载地图提取数据：${String(catalogError)}`)}</span>}
      {!extracts && !catalogError && <span className="hint">{L('Loading the extract catalog…', '正在加载提取数据目录…')}</span>}
    </label>

    <RegionMap
      L={L}
      basemap={basemap}
      basemapLoading={!catalog && !catalogError}
      coverage={extractBounds ?? null}
      bounds={formBounds}
      onBoundsChange={onMapBounds}
      geometry={mapGeometry}
      instances={mapObjects.instances}
    />
    {!!mapObjects.invalidPositions.length && <p className="wb-field-error" role="alert">{L(
      `Fix invalid positions to show these markers: ${mapObjects.invalidPositions.join(', ')}`,
      `请修正以下无效位置以显示标记：${mapObjects.invalidPositions.join('、')}`)}</p>}
    {mapGeometry?.truncated && <span className="wb-map-chip warn" role="status">{L('Preview truncated — showing up to 3000 buildings and 3000 roads', '预览已截断——最多显示 3000 栋建筑和 3000 条道路')}</span>}

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
      <button className="wb-btn" disabled={invalid || previewing || applying} onClick={() => void doPreview()}>{previewing ? L('Previewing…', '预演中…') : L('Preview region', '预览区域')}</button>
      <button className="wb-btn primary" data-testid="apply-region" disabled={invalid || !preview || stale || previewing || applying || (needsConfirm && !acknowledged)}
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

    {preview && <div className="wb-region-card wb-region-review" data-testid="region-review" role="group" aria-label={L('Region review', '区域评审')} aria-disabled={stale || undefined}>
      <h4>{L('Review before applying', '应用前评审')}{stale && <span className="wb-tag warn">{L('Out of date — preview again', '已失效——请重新预览')}</span>}</h4>
      <dl className="wb-region-meta">
        <dt>{L('Extract', '提取数据')}</dt><dd className="wb-mono">{preview.extract}</dd>
        <dt>{L('Area (W, S, E, N)', '范围（西、南、东、北）')}</dt><dd className="wb-mono">{preview.bounds.map(value => value.toFixed(5)).join(', ')}</dd>
        <dt>{L('Buildings', '建筑物')}</dt><dd className="wb-mono">{preview.buildings}</dd>
        <dt>{L('Roads', '道路')}</dt><dd>{preview.roads}</dd>
        <dt>{L('Attribution', '来源署名')}</dt><dd>{preview.attribution || L('unknown', '未知')}</dd>
      </dl>
      {(preview.origin || preview.height_source || !!preview.previous_origin || !!preview.diagnostics.length) && <details className="wb-region-details">
        <summary>{L('Details', '详情')}</summary>
        <dl className="wb-region-meta">
          <dt>{L('ENU origin', 'ENU 原点')}</dt><dd className="wb-mono">{coord(preview.origin.lat)}, {coord(preview.origin.lon)} · {L('altitude', '海拔')} {m(preview.origin.alt, 0)}</dd>
          <dt>{L('Height source', '高度来源')}</dt><dd className="wb-mono">{preview.height_source ? serverText(preview.height_source, L) : L('Not provided', '未提供')}</dd>
        </dl>
        {!!preview.previous_origin && <p className="hint">{L(
          `Origin moves ${preview.origin_shift_m == null ? L('by an unknown distance', '未知距离') : m(preview.origin_shift_m)}; affected objects ${preview.affected_entities.length ? preview.affected_entities.join(', ') : L('none', '无')} keep their ENU coordinates, so their geographic position changes with the origin.`,
          `原点移动 ${preview.origin_shift_m == null ? '未知距离' : m(preview.origin_shift_m)}；受影响对象 ${preview.affected_entities.length ? preview.affected_entities.join('、') : '无'} 的 ENU 坐标不变，其地理位置随原点改变。`)}</p>}
        {!!preview.diagnostics.length && <ul className="wb-region-diag">{preview.diagnostics.map((line, index) => <li key={index} className="wb-mono">{serverText(line, L)}</li>)}</ul>}
      </details>}
      {heightSummaryFromPreview(preview, L) && <p className="hint" role="status">{heightSummaryFromPreview(preview, L)}</p>}
      {preview.network_invalidated && <p className="wb-region-warn" role="status">⚠ {L('Applying replaces the region, so the previously generated road network becomes stale and must be regenerated.', '应用会替换区域，已生成的路网将失效，需要重新生成。')}</p>}
      {preview.city_conflict && <p className="wb-region-warn" role="status">⚠ {L('A fixed city is also configured. The run must use one source; clear the city below or skip the region.', '同时配置了固定城市。运行只能使用一个数据源：请在下方清除城市，或放弃该区域。')}</p>}
      {!preview.geometry && <p className="wb-region-warn" role="status">{L('The preview contains no building or road geometry; update the server to review the map before applying.', '预览未提供建筑或道路几何；请更新服务器以便在应用前查看地图。')}</p>}
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
