import { expect, it } from 'vitest';
import { initialActionFraming } from './action-framing';
import type { RunHeader } from '../contracts/viewer-feed';
import type { EntityState } from './feed-store';

it('frames committed main actors, excludes background traffic and waits for the bootstrap', () => {
  const header = { presentation: [{ typeId: 'vehicle', positionField: 'position', frame: 'enu' }], types: [] } as unknown as RunHeader;
  const actor = (id: string, role: string, position: number[]): EntityState => ({
    key: { id, generation: 0 }, typeId: 'vehicle', fields: new Map([
      ['traffic.actor.role', { value: role }], ['position', { value: position }],
    ]) as EntityState['fields'],
  });
  const actors = [actor('reporter', 'reporter', [0, 0, 0]), actor('incident', 'incident', [20, 0, 0]), actor('responder', 'candidate', [10, 0, 100])];
  expect(initialActionFraming(header, actors.slice(0, 2))).toBeUndefined();
  expect(initialActionFraming(header, [...actors, actor('background', 'background', [9000, 9000, 0])])).toEqual({ center: [10, 0, 50], radius: Math.hypot(10, 50) });
});

it('frames each ordinary template from its committed spatial objects, converting the declared coordinates', () => {
  const header = { presentation: [{ typeId: 'site', positionField: 'position', frame: 'enu' }, { typeId: 'vehicle', positionField: 'position', frame: 'ned' }], types: [] } as unknown as RunHeader;
  const actor = (id: string, typeId: string, position: number[]): EntityState => ({ key: { id, generation: 0 }, typeId,
    fields: new Map([['position', { value: position }]]) as EntityState['fields'] });
  const actors = [actor('site', 'site', [2000, 1000, 20]), actor('uav', 'vehicle', [1000, 2020, -40]), actor('order', 'order', [0, 0, 0])];
  expect(initialActionFraming(header, actors)).toEqual({ center: [2010, 1000, 30], radius: 25 });
  expect(initialActionFraming(header, actors.slice(2))).toBeUndefined();
});

it('includes the other business actors in a shared city after the incident bootstrap arrives', () => {
  const header = { presentation: [{ typeId: 'vehicle', positionField: 'position', frame: 'enu' }], types: [] } as unknown as RunHeader;
  const actor = (id: string, position: number[], role?: string): EntityState => ({ key: { id, generation: 0 }, typeId: 'vehicle',
    fields: new Map<string, { value: unknown }>([['position', { value: position }], ...(role ? [['traffic.actor.role', { value: role }] as [string, { value: unknown }]] : [])]) as EntityState['fields'] });
  const actors = [actor('reporter', [0, 0, 0], 'reporter'), actor('incident', [20, 0, 0], 'incident'), actor('responder', [10, 0, 100], 'candidate'), actor('courier', [1000, 0, 10]), actor('traffic', [9000, 9000, 0], 'background')];
  expect(initialActionFraming(header, actors)?.center).toEqual([500, 0, 50]);
});
