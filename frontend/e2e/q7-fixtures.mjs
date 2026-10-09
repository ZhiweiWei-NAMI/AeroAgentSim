/** Explicit load-test records; never used as production run data. */
export function syntheticFeed(count, graph = false) {
  const at = ns => ({ ns: String(ns), microstep: 0 });
  const header = {
    contract: 'aeroagentsim.viewer-feed/v1', runId: `q7-synthetic-${count}${graph ? '-graph' : ''}`,
    registryDigest: 'explicit-synthetic-benchmark', start: at(0), end: at(10_000_000_000),
    types: [
      { typeId: 'load:Record', displayName: 'Load-test record', ancestors: [], directory: 'Benchmark fixture' },
      ...Array.from({ length: graph ? 4 : 1 }, (_, i) => ({ typeId: `load:Type${i}`, displayName: `Fixture type ${i}`, ancestors: ['load:Record'], directory: 'Benchmark fixture' })),
    ],
    fields: [
      { fieldId: 'load:state', displayName: 'Recorded state', valueType: 'string', role: 'state' },
      { fieldId: 'load:value', displayName: 'Recorded value', valueType: 'number', role: 'state' },
      ...(!graph ? [{ fieldId: 'load:position', displayName: 'Position', valueType: 'vector', unit: 'm', frame: 'enu' }] : []),
    ],
    presentation: graph ? [] : [{ typeId: 'load:Record', positionField: 'load:position', frame: 'enu', visual: { kind: 'marker', color: '#50c9ff' } }],
  };
  const keys = Array.from({ length: count }, (_, i) => ({ id: `fixture-${String(i).padStart(4, '0')}`, generation: 0 }));
  const states = ['waiting', 'running', 'done'];
  const edge = i => ({ edgeId: `fixture-edge-${i}`, relationId: 'load:linked', source: keys[i % count], target: keys[(i * 7 + 1) % count], op: 'assert', validFrom: at(0) });
  const commits = Array.from({ length: 41 }, (_, step) => {
    const time = at(step * 250_000_000);
    const facts = keys.flatMap((entity, i) => [
      { entity, fieldId: 'load:state', value: states[(i + step) % 3], producer: 'explicit-load-fixture', validFrom: time },
      { entity, fieldId: 'load:value', value: i + step, producer: 'explicit-load-fixture', validFrom: time },
      ...(!graph ? [{ entity, fieldId: 'load:position', value: [(i % 50) * 6 + step, Math.floor(i / 50) * 6, 10 + i % 4], producer: 'explicit-load-fixture', validFrom: time }] : []),
    ]);
    return { commitIndex: step + 1, at: time,
      created: step === 0 ? keys.map((key, i) => ({ ...key, typeId: `load:Type${graph ? i % 4 : 0}` })) : [],
      removed: [], facts, retracted: [],
      edges: !graph ? [] : step === 0 ? Array.from({ length: 5000 }, (_, i) => edge(i)) : step === 20 ? Array.from({ length: 100 }, (_, i) => ({ ...edge(i), op: 'close', validTo: time })) : [],
      messages: [], receipts: [],
    };
  });
  return { header, commits };
}
