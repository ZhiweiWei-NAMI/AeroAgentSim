import { describe, expect, it } from 'vitest';
import { enuPosition } from '../viewport/position';
import { draftBinding, previewPositions } from './preview-positions';

const origin = { lat: 31.2304, lon: 121.4737, alt: 12 };
const wgs84Position: [number, number, number] = [31.2305, 121.4738, 42];
const reference = enuPosition(wgs84Position, 'wgs84', origin);
const marker = { kind: 'marker' as const };

describe('previewPositions', () => {
  it('maps ENU, NED and WGS84 declarations to the same runtime world point', () => {
    const scenario = {
      origin,
      presentation: [
        { typeId: 'frame:Enu', positionField: 'pose', frame: 'enu', visual: marker },
        { typeId: 'frame:Ned', positionField: 'pose', frame: 'ned', visual: marker },
        { typeId: 'frame:Wgs84', positionField: 'pose', frame: 'wgs84', visual: marker },
      ],
      entities: [
        { id: 'enu', type: 'frame:Enu', facts: { pose: [reference[0], reference[1], reference[2]] } },
        { id: 'ned', type: 'frame:Ned', facts: { pose: [reference[1], reference[0], -reference[2]] } },
        { id: 'wgs84', type: 'frame:Wgs84', facts: { pose: wgs84Position } },
      ],
    };

    const result = previewPositions(scenario);
    expect(result.diagnostics).toEqual([]);
    expect(result.placements.map(placement => placement.id).sort()).toEqual(['enu', 'ned', 'wgs84']);
    for (const placement of result.placements) {
      expect(placement.enu[0]).toBeCloseTo(reference[0], 9);
      expect(placement.enu[1]).toBeCloseTo(reference[1], 9);
      expect(placement.enu[2]).toBeCloseTo(reference[2], 9);
      expect(placement.world[0]).toBeCloseTo(reference[0], 9);
      expect(placement.world[1]).toBeCloseTo(reference[2], 9);
      expect(placement.world[2]).toBeCloseTo(-reference[1], 9);
    }
  });

  it('resolves a position field inherited from an ancestor type', () => {
    const scenario = {
      registry: { types: [{ id: 'Site', parents: [] }, { id: 'Restaurant', parents: ['Site'] }] },
      presentation: [{ typeId: 'Site', positionField: 'site.position', frame: 'enu', visual: marker }],
      entities: [{ id: 'kitchen', type: 'Restaurant', facts: { 'site.position': [10, 20, 30] } }],
    };

    expect(draftBinding(scenario, 'Restaurant')).toBe(scenario.presentation[0]);
    const result = previewPositions(scenario);
    expect(result.placements).toEqual([expect.objectContaining({ id: 'kitchen', enu: [10, 20, 30], world: [10, 30, -20] })]);
    expect(result.hasSpatialEntities).toBe(true);
  });

  it('reports a missing origin instead of fabricating a WGS84 position', () => {
    const result = previewPositions({
      presentation: [{ typeId: 'frame:Wgs84', positionField: 'pose', frame: 'wgs84', visual: marker }],
      entities: [{ id: 'plane', type: 'frame:Wgs84', facts: { pose: wgs84Position } }],
    });

    expect(result.placements).toEqual([]);
    expect(result.diagnostics).toEqual([expect.objectContaining({ entityId: 'plane', typeId: 'frame:Wgs84', field: 'pose', code: 'missing-origin' })]);
    expect(result.hasSpatialEntities).toBe(true);
  });

  it('reports an unsupported frame instead of guessing axes', () => {
    const result = previewPositions({
      presentation: [{ typeId: 'frame:Unknown', positionField: 'pose', frame: 'polar', visual: marker }],
      entities: [{ id: 'unknown', type: 'frame:Unknown', facts: { pose: [1, 2, 3] } }],
    });

    expect(result.placements).toEqual([]);
    expect(result.diagnostics).toEqual([expect.objectContaining({ entityId: 'unknown', code: 'invalid-frame' })]);
  });

  it('counts a custom bound type as spatial and leaves an unbound type nonspatial', () => {
    const scenario = {
      presentation: [{ typeId: 'custom:Thing', positionField: 'custom.location', frame: 'ned', visual: marker }],
      entities: [
        { id: 'thing', type: 'custom:Thing', facts: { 'custom.location': [3, 2, -4] } },
        { id: 'task', type: 'custom:Task', facts: { status: 'open' } },
      ],
    };

    const result = previewPositions(scenario);
    // ScenePreview uses this flag for its "No places yet" empty state.
    expect(result.hasSpatialEntities).toBe(true);
    expect(result.placements).toEqual([expect.objectContaining({ id: 'thing', typeId: 'custom:Thing', enu: [2, 3, 4], world: [2, 4, -3] })]);
    expect(result.diagnostics).toEqual([]);
  });

  it('never places a position-shaped fact without a presentation binding', () => {
    const result = previewPositions({
      entities: [{ id: 'task', type: 'custom:Task', facts: { 'custom.location': [1, 2, 3] } }],
    });

    expect(result.placements).toEqual([]);
    expect(result.hasSpatialEntities).toBe(false);
    expect(result.diagnostics).toEqual([]);
  });

  it('keeps configured fleet starts as spatial ENU declarations', () => {
    const result = previewPositions({ generators: { fleet: { count: 2, starts: [[1, 2, 3]] } } });

    expect(result.hasSpatialEntities).toBe(true);
    expect(result.placements).toEqual([expect.objectContaining({ id: 'fleet:start:1', frame: 'enu', enu: [1, 2, 3], world: [1, 3, -2], source: 'generator' })]);
  });
});
