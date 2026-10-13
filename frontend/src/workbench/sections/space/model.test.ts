import { describe, expect, it, vi } from 'vitest';
import { WorkbenchApi } from '../../api';
import { regionMapObjects } from '../objects/RegionMapSupport';
import { catalogBasemap, extractLabel, fetchMapCatalog, heightSummary, previewMapGeometry, regionDraft, sceneMapGeometry } from './model';
import { APPLIED_REGION, BASEMAP, DRAFT, PREVIEW, SCENE } from './mapTestFixtures';

const L = (en: string, _zh: string) => en;

describe('real region source contracts', () => {
  it('keeps the preview ENU frame distinct from saved WGS84 footprints and retains road records', () => {
    const applied = sceneMapGeometry(SCENE)!;
    const preview = previewMapGeometry(PREVIEW);
    expect(applied.buildingFrame).toBe('wgs84');
    expect(preview.buildingFrame).toBe('enu');
    expect(applied.buildings).toBe(SCENE!.geojson!.features);
    expect(preview.buildings).toBe(PREVIEW.geometry!.buildings.features);
    expect(applied.roads).toBe(SCENE!.roads);
    expect(preview.roads).toBe(PREVIEW.geometry!.roads);
    expect(preview.truncated).toBe(false);
    expect(previewMapGeometry({ ...PREVIEW, geometry: { ...PREVIEW.geometry!, truncated: true } }).truncated).toBe(true);
  });

  it('hydrates authored values without turning missing/nonfinite settings into modelling defaults', () => {
    expect(regionDraft(APPLIED_REGION)).toMatchObject({ alt: '25', level: '6', extract: 'wujiaochang' });
    expect(regionDraft({ ...APPLIED_REGION, alt: NaN, level_height_m: undefined as unknown as number })).toMatchObject({ alt: '', level: '' });
    expect(regionDraft()).toMatchObject({ alt: '0', level: '3.5' });
  });

  it('rejects a malformed catalog instead of synthesising a successful empty list', async () => {
    const api = Object.assign(new WorkbenchApi('http://api.test'), { request: vi.fn(async () => ({})) });
    await expect(fetchMapCatalog(api)).rejects.toThrow(/no extract list/);
  });

  it('respects an extract basemap override, including explicit disabling, and displays only supplied counts', () => {
    const catalog = { basemap: BASEMAP, extracts: [{ id: 'disabled', basemap: null }, { id: 'normal' }] };
    expect(catalogBasemap(catalog, 'normal')).toBe(BASEMAP);
    expect(catalogBasemap(catalog, 'disabled')).toBeNull();
    expect(extractLabel({ id: 'plain', label: 'Plain' }, L)).toBe('Plain');
    expect(extractLabel({ id: 'empty', buildings: 0, roads: 0 }, L)).toBe('empty — 0 buildings · 0 roads');
    expect(extractLabel({ id: 'city', label: 'City', label_zh: '城市', buildings: 414, roads: 120 }, (_en, zh) => zh)).toBe('城市 — 414 栋建筑 · 120 条道路');
  });

  it('height accounting excludes road features', () => {
    expect(heightSummary({ ...SCENE, geojson: { features: [...SCENE!.geojson!.features!, { geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] } }] } })).toEqual({ sourced: 1, estimated: 1, missing: 1 });
  });
});

describe('places and fleet starts on the map', () => {
  it('uses site/UAV facts and generic type-specific presentation positions, with active-locale role markers', () => {
    const result = regionMapObjects({ ...DRAFT, entities: [...DRAFT.entities,
      { id: 'generic', type: 'aas:Custom', facts: { 'custom.position': [12, 34, 56] } }],
      presentation: [...DRAFT.presentation, { typeId: 'aas:Custom', positionField: 'custom.position', visual: { color: '#1f7ae0' } }],
    }, (_en, zh) => zh);
    expect(result.instances).toHaveLength(4);
    expect(result.instances[0]).toMatchObject({ east: 100, north: 0, color: '#e0453a', letter: '餐', label: '餐厅 · kitchen' });
    expect(result.instances[2]).toMatchObject({ east: 100, north: 10, letter: '配' });
    expect(result.instances[3]).toMatchObject({ east: 12, north: 34, color: '#1f7ae0' });
    expect(result.invalidPositions).toEqual([]);
  });

  it('uses the actual task role for medical drones and draws configured generator starts without expanding fake members', () => {
    const result = regionMapObjects({ ...DRAFT,
      engines: { dispatch: { plugin: 'lowalt_tasks', config: { vehicle_task_kinds: { 'courier-1': ['medical'] } } } },
      generators: { fleet: { prefix: 'fleet-', count: 20, starts: [[12, 34, 56], [-10, 40, 0]], facts: { 'lowalt.vehicle_class': 'inspection' } } },
    }, L);
    expect(result.instances).toHaveLength(5);
    expect(result.instances[2]).toMatchObject({ color: '#e0453a', letter: 'M' });
    expect(result.instances[3]).toMatchObject({ east: 12, north: 34, letter: 'I' });
    expect(result.instances[4]).toMatchObject({ east: -10, north: 40, letter: 'I' });
  });

  it('reports invalid stored coordinates rather than silently making a zero marker', () => {
    const result = regionMapObjects({ entities: [{ id: 'bad', type: 'lowalt:Customer', facts: { 'lowalt.site_position': null } }],
      generators: { fleet: { starts: [[null, 2, 0]] } } }, L);
    expect(result.instances).toEqual([]);
    expect(result.invalidPositions).toEqual(['bad', 'Fleet start 1']);
  });
});
