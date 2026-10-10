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
