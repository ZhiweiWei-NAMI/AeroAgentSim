import {
  dedupeLatLngPoints,
  sanitizeLatLngPoints,
  shouldUsePointViewport,
  toLatLng,
} from './map2dUtils';

describe('map2dUtils', () => {
  test('toLatLng returns null for invalid values', () => {
    expect(toLatLng({ x: 'bad', y: 10 }, 'simulation_plane')).toBeNull();
    expect(toLatLng({ lat: 30, lng: Infinity }, 'geo_osm')).toBeNull();
    expect(toLatLng(null, 'simulation_plane')).toBeNull();
  });

  test('sanitizeLatLngPoints removes invalid points', () => {
    expect(
      sanitizeLatLngPoints([
        [10, 20],
        null,
        [Number.NaN, 30],
        [15, 25],
      ])
    ).toEqual([
      [10, 20],
      [15, 25],
    ]);
  });

  test('shouldUsePointViewport collapses identical trajectory samples', () => {
    const points = [
      [80, 120],
      [80, 120],
      [80.0000001, 120.0000001],
    ];
    expect(dedupeLatLngPoints(points)).toEqual([[80, 120]]);
    expect(shouldUsePointViewport(points)).toBe(true);
  });
});
