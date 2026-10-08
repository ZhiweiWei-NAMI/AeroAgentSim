import type { FeedCommit, RunHeader, ViewerFeed } from '../contracts/viewer-feed';

/** Explicit authored demonstration. Never used as a replacement for a failed service. */
export function demoFeed(): ViewerFeed {
  const header: RunHeader = {
    contract: 'aeroagentsim.viewer-feed/v1', runId: 'authored-viewer-demo', registryDigest: 'demo-fixture-v1',
    start: { ns: '9007199254740993000', microstep: 0 }, end: { ns: '9007199278740993000', microstep: 0 },
    types: [
      { typeId: 'demo:Spatial', displayName: 'Spatial object', ancestors: [] },
      { typeId: 'demo:Amber', displayName: 'Amber group', ancestors: ['demo:Spatial'] },
      { typeId: 'demo:Teal', displayName: 'Teal group', ancestors: ['demo:Spatial'] },
      { typeId: 'demo:Violet', displayName: 'Violet group', ancestors: ['demo:Spatial'] },
      { typeId: 'demo:Record', displayName: 'Record', ancestors: [] },
    ],
    fields: [
      { fieldId: 'demo:position', displayName: 'Position', role: 'spatial position', unit: 'm', frame: 'enu', valueType: 'vector3' },
      { fieldId: 'demo:rotation', displayName: 'Orientation', role: 'body orientation', unit: null, frame: 'enu', valueType: 'quaternion' },
      { fieldId: 'demo:status', displayName: 'Status', role: 'record state', unit: null, valueType: 'string' },
    ],
    presentation: [
      { typeId: 'demo:Spatial', positionField: 'demo:position', orientationField: 'demo:rotation', frame: 'enu', visual: { kind: 'marker', scale: 1.2, color: '#df9649' } },
      { typeId: 'demo:Teal', positionField: 'demo:position', orientationField: 'demo:rotation', frame: 'enu', visual: { kind: 'marker', scale: 1.2, color: '#168b92' } },
      { typeId: 'demo:Violet', positionField: 'demo:position', orientationField: 'demo:rotation', frame: 'enu', visual: { kind: 'marker', scale: 1.2, color: '#8074ba' } },
    ],
  };
  const key = (i: number) => ({ id: `object-${String(i + 1).padStart(2, '0')}`, generation: 0 });
  const record = (i: number) => ({ id: `record-${i + 1}`, generation: 0 });
  const commits: FeedCommit[] = [];
  for (let step = 0; step <= 240; step++) {
    const at = { ns: (BigInt(header.start.ns) + BigInt(step) * 100_000_000n).toString(), microstep: 0 };
    const facts: FeedCommit['facts'] = [];
    for (let i = 0; i < 50; i++) {
      const group = i % 3, lane = Math.floor(i / 3), phase = lane * Math.PI * 2 / 17 + step / 240 * Math.PI * 2;
      const radius = 22 + group * 15;
      const position = [radius * Math.cos(phase), radius * Math.sin(phase), 2.5 + group * 4 + 2 * Math.sin(phase * 2 + lane)];
      facts.push({ entity: key(i), fieldId: 'demo:position', value: position, producer: 'authored-path-fixture', validFrom: at });
      facts.push({ entity: key(i), fieldId: 'demo:rotation', value: [0, 0, Math.sin(phase / 2), Math.cos(phase / 2)], producer: 'authored-path-fixture', validFrom: at });
    }
    if (step === 0) for (let i = 0; i < 3; i++) facts.push({ entity: record(i), fieldId: 'demo:status', value: ['scheduled', 'available', 'recorded'][i], producer: 'authored-record-fixture', validFrom: at });
    const commandId = `demo-command-${step}`;
    commits.push({ commitIndex: step, at,
      created: step === 0 ? [
        ...Array.from({ length: 50 }, (_, i) => ({ ...key(i), typeId: ['demo:Amber', 'demo:Teal', 'demo:Violet'][i % 3] })),
        ...Array.from({ length: 3 }, (_, i) => ({ ...record(i), typeId: 'demo:Record' })),
      ] : [], removed: [], facts, retracted: [],
      edges: step === 0 ? [{ edgeId: 'demo-edge', relationId: 'demo:references', source: record(0), target: key(0), op: 'assert' }] : [],
      messages: step % 20 === 0 ? [{ id: commandId, kind: 'command', schemaId: 'demo:observe', source: record(0).id, target: key(0).id, at, payload: { note: 'Authored demonstration receipt' } }] : [],
      receipts: step % 20 === 0 ? [{ commandId, status: 'accepted', result: { fixture: true } }] : [],
    });
  }
  return { header: async () => header, subscribe: async (from, onCommit, signal) => {
    for (const commit of commits) { if (signal?.aborted) return; if (commit.commitIndex >= from) onCommit(commit); }
  } };
}
