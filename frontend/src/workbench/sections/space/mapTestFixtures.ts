import type { CompiledRegion, RegionPreview } from './model';

// Coordinates/road records from T1's retained wujiaochang preview/workspace evidence.
export const COVERAGE = [121.500562, 31.3002, 121.5258365, 31.3066321];
export const ORIGIN = { lat: 31.30341605, lon: 121.51319925, alt: 25 };
const ENU_RINGS = [
  [[-68.85378638780476, -202.60687177275582, 0], [-91.23543198855663, -210.45680009595986, 0], [-93.49168465001041, -212.51906940129183, 0], [-68.85378638780476, -202.60687177275582, 0]],
  [[-1203.072951313621, -342.24246118361435, 0], [-1197.922602411298, -356.5785590961118, 0], [-1130.2826228346837, -332.49657559619305, 0], [-1203.072951313621, -342.24246118361435, 0]],
  [[-1016.7845459142267, -321.7639052331718, 0], [-1011.2438748216969, -337.17548767579507, 0], [-940.7097990042133, -312.0623695230134, 0], [-1016.7845459142267, -321.7639052331718, 0]],
];
export const WGS_RINGS = [
  [[121.512476, 31.3015887], [121.5122409, 31.3015179], [121.5122172, 31.3014993], [121.512476, 31.3015887]],
  [[121.500562, 31.3003293], [121.5006161, 31.3002], [121.5013266, 31.3004172], [121.500562, 31.3003293]],
  [[121.5025188, 31.300514], [121.502577, 31.300375], [121.5033179, 31.3006015], [121.5025188, 31.300514]],
];
export const ROADS = [{ id: 'way/10487419', points: [[1203.072951313621, 356.5785590957179, 0], [591.3523985064483, 140.6833552675966, 0]], tags: { highway: 'trunk' } }];
const features = (rings: number[][][]) => rings.map((ring, index) => ({
  properties: index === 0 ? { height_source: 'osm.height' } : index === 1 ? { height_source: 'osm.building:levels * authored level_height_m' } : {},
  geometry: { type: 'Polygon', coordinates: [ring] },
}));
export const APPLIED_REGION: CompiledRegion['region'] = { extract: 'wujiaochang', bounds: COVERAGE, alt: 25, level_height_m: 6 };
export const SCENE: CompiledRegion['scene'] = { origin: ORIGIN, ground: { width_m: 2406.1459, depth_m: 713.1571 },
  geojson: { features: features(WGS_RINGS) }, roads: ROADS };
export const PREVIEW: RegionPreview['preview'] = {
  extract: 'wujiaochang', bounds: COVERAGE, origin: ORIGIN, previous_origin: null, origin_shift_m: null,
  buildings: 3, roads: 1, attribution: '© OpenStreetMap contributors · ODbL',
  height_source: 'OSM height; building levels / OSM 高度；建筑层数', diagnostics: [],
  affected_entities: [], network_invalidated: false, city_conflict: false,
  geometry: { buildings: { features: features(ENU_RINGS) }, roads: ROADS, truncated: false },
};
export const BASEMAP = { url: 'https://tiles.test/{z}/{x}/{y}.png', attribution: '© Test tiles', max_zoom: 18 };
export const DRAFT = {
  entities: [
    { id: 'kitchen', type: 'lowalt:Restaurant', label: 'Kitchen', label_zh: '餐厅', facts: { 'lowalt.site_position': [100, 0, 0] } },
    { id: 'pad', type: 'lowalt:ChargingPad', facts: { 'lowalt.site_position': [-80, 0, 0] } },
    { id: 'courier-1', type: 'lowalt:UAV', label: 'Meal courier 1', label_zh: '送餐无人机 1', facts: { 'lowalt.position': [100, 10, 0], 'lowalt.vehicle_class': 'parcel' } },
    { id: 'weather', type: 'lowalt:Environment', facts: {} },
  ],
  presentation: [{ typeId: 'lowalt:UAV', positionField: 'lowalt.position' }, { typeId: 'lowalt:Site', positionField: 'lowalt.site_position' }],
};
