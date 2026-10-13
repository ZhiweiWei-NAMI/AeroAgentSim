import { useEffect, useMemo, useState } from 'react';
import { ApiError, type WorkbenchApi } from '../../api';
import { validEnuOrigin, validWgs84Bounds } from '../../geo';
import type { Scenario, SectionProps } from '../types';
import { regionMapObjects } from '../objects/RegionMapSupport';
import { RegionMap } from './RegionMap';
import { catalogBasemap, fetchMapCatalog, readRegion, sceneMapGeometry, type MapCatalog } from './model';

export interface PlacePickerMapProps {
  api: WorkbenchApi;
  /** Full GET workspace record (AddPlace/AddObjects: project_detail ?? project).
   * An applied region plus its compiled scene/origin are required; draft.origin alone is insufficient.
   */
  workspace: { region?: unknown; scene?: unknown };
  /** Current scenario, used for existing places and fleet start markers. */
  draft: Scenario;
  /** Controlled ENU [east, north] metres. Parse the live x/y fields and pass null if either
   * is empty/invalid. Do not keep a separate pickMarker state: typed edits must move this marker.
   */
  position: [number, number] | null;
  /** Converts a map click with the applied scene's origin; inside refers to applied bounds.
   * Write east/north to the form and invalidate its review. Keep up and the picker open;
   * place creation/update remains the caller's C11 preview/apply responsibility.
   */
  onPick: (east: number, north: number, inside: boolean) => void;
  L: SectionProps['L'];
  onOpenRegion?: () => void;
}

export function canPickOnMap(workspace: PlacePickerMapProps['workspace']): boolean {
  const region = readRegion(workspace);
  return validWgs84Bounds(region?.region.bounds) && validEnuOrigin(region?.scene?.origin);
}

/** Reusable picker: same C9 tiles, applied bounds, real buildings/roads and role markers as Region.
 * Mount in AddPlace/AddObjects whenever `picking` is true, including during ENU input.
 * Pass `project_detail ?? project`, the current draft and live parsed east/north, or null
 * if either coordinate is invalid. Opening AddPlace's picker selects ENU mode and shows
 * its coordinate fields. A click updates east/north (to one decimal place), preserves up
 * and invalidates the caller's review; typed changes move the same controlled marker.
 * Gate the opening action with canPickOnMap; draft.origin alone is insufficient. Keep
 * the inline region prerequisite, optionally using onOpenRegion for a navigation button.
 * This component never authors objects: creation/update uses the caller's reviewed flow.
 */
export function PlacePickerMap({ api, workspace, draft, position, onPick, onOpenRegion, L }: PlacePickerMapProps) {
  const [catalog, setCatalog] = useState<MapCatalog>();
  const [problem, setProblem] = useState<unknown>();
  const region = useMemo(() => readRegion(workspace), [workspace]);
  const ready = canPickOnMap(workspace);
  const geometry = useMemo(() => sceneMapGeometry(region?.scene), [region]);
  const objects = useMemo(() => regionMapObjects(draft, L), [draft.entities, draft.presentation, draft.generators, draft.engines, L]);

  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    setProblem(undefined); setCatalog(undefined);
    void fetchMapCatalog(api, controller.signal).then(answer => {
      if (!controller.signal.aborted) setCatalog(answer);
    }).catch(error => { if (!controller.signal.aborted) setProblem(error); });
    return () => controller.abort();
  }, [api, ready]);

  if (!ready) return <div className="wb-empty" role="status">
    <p>{L('Apply a region in Scene & space to pick a position on the map.', '请在场景与空间中应用区域，以便在地图上选点。')}</p>
    {onOpenRegion && <button type="button" className="wb-btn small" onClick={onOpenRegion}>{L('Select region', '选择区域')}</button>}
  </div>;

  return <div>
    {!!objects.invalidPositions.length && <p className="wb-field-error" role="alert">{L(
      `Fix invalid positions to show these markers: ${objects.invalidPositions.join(', ')}`,
      `请修正以下无效位置以显示标记：${objects.invalidPositions.join('、')}`)}</p>}
    {!!problem && <p className="wb-field-error" role="alert">{problem instanceof ApiError && problem.unavailable
      ? L('Map background unavailable — applied bounds and geometry are still shown.', '地图底图不可用——仍显示已应用范围与几何。')
      : L(`Could not load the map background: ${String(problem)}`, `无法加载地图底图：${String(problem)}`)}</p>}
    <RegionMap L={L} height={260} basemap={catalogBasemap(catalog, region!.region.extract)}
      basemapLoading={!catalog && !problem} coverage={region!.region.bounds} bounds={region!.region.bounds}
      geometry={geometry} instances={objects.instances} pick={{ onPick, marker: position }}
      outsideWarning={L('Outside the applied region — this point is outside the compiled area.', '超出已应用区域——此点位于已编译范围外。')} />
  </div>;
}
