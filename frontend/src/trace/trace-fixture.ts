import { TemporalFeedStore } from '../feeds/temporal-store';
import type { ChainInstance, FeedCommit, PredicateTruth, RunHeader } from '../contracts/viewer-feed';

const at = (ns: string, microstep = 0) => ({ ns, microstep });
export const incident = { id: 'incident.1', generation: 0 }, car = { id: 'car.1', generation: 0 }, cam = { id: 'cam.1', generation: 0 };
const ref = (key: { id: string; generation: number }) => ({ $ref: { id: key.id, generation: key.generation, type_id: 'x' } });
const header: RunHeader = {
  contract: 'aeroagentsim.viewer-feed/v1', runId: 'r', epoch: 'e', registryDigest: 'd', start: at('0'),
  types: [{ typeId: 'car', displayName: 'Car', ancestors: [] }, { typeId: 'incident', displayName: 'Incident', ancestors: [] }, { typeId: 'cam', displayName: 'Camera', ancestors: [] }],
  fields: [
    { fieldId: 'demo.car.speed_mps', displayName: 'demo.car.speed_mps', valueType: 'number', unit: 'm/s' },
    { fieldId: 'demo.incident.active', displayName: 'demo.incident.active', valueType: 'boolean', metadata: { writer_contract: 'behaviour' } },
  ],
  presentation: [],
  behaviour: {
    extensions: [], packages: [{
      predicates: { 'demo.p.stopped': { expression: { op: 'eq', args: [{ field: 'demo.car.speed_mps', role: 'vehicle', path: [] }, { literal: 0 }] }, parameters: {} } },
      chains: {
        'demo.alert': { transitions: [
          { id: 'raise', from: 'idle', to: 'raised', on: { predicate: 'demo.p.stopped' }, actions: [{ id: 'set-active', kind: 'set', entity: { $role: 'incident' }, field: 'demo.incident.active', value: true }, { id: 'say', kind: 'emit', schema: 'demo.alert.raised', payload: { incident: { $role: 'incident' } } }] },
        ] },
        'demo.watch': { transitions: [{ id: 'notice', from: 'wait', to: 'seen', on: { event: 'demo.alert.raised' }, actions: [] }] },
      },
    }],
  } as RunHeader['behaviour'],
};
const empty = (commitIndex: number, ns: string, microstep = 0): FeedCommit => ({ commitIndex, at: at(ns, microstep), created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
const evaluation = { acquired: { 'vehicle.demo.car.speed_mps': { clockId: 'c', mappingId: 'm', numerator: '1000000000', producer: 'motion' } }, contextId: 'predicate:1', diagnostics: [], evaluatedAt: at('1000000000', 1), op: 'assert', predicateId: 'demo.p.stopped', profile: 'p', roles: { vehicle: ref(car) }, readCut: { index: 2, at: at('1000000000') } };
const truth: PredicateTruth = { contextId: 'predicate:1', predicateId: 'demo.p.stopped', roles: { vehicle: car }, profile: 'p', status: 'known', value: true, diagnostics: [], evaluatedAt: at('1000000000', 1), readCut: { index: 2, at: at('1000000000') }, validFrom: at('1000000000', 1), op: 'assert', acquired: evaluation.acquired };
const raise: ChainInstance = { instanceId: 'chain:a', templateId: 'demo.alert', bindingId: 'b', packageDigest: 'p', roles: { incident, vehicle: car }, lifecycle: 'transitioned', state: 'raised', revision: 1, variables: {}, children: {}, validFrom: at('1000000000', 2), op: 'assert', transitionId: 'raise', trigger: { predicate: 'demo.p.stopped', edge: 'entered', evaluation } };
const notice: ChainInstance = { ...raise, instanceId: 'chain:b', templateId: 'demo.watch', roles: { camera: cam, incident }, state: 'seen', transitionId: 'notice', trigger: { event: 'demo.alert.raised', payload: { incident: ref(incident) } } };
const message = { id: 'm1', kind: 'event' as const, schemaId: 'demo.alert.raised', source: 'behaviour', at: at('1000000000', 2), payload: { incident: ref(incident) }, subjects: [incident] };
const commits: FeedCommit[] = [
  { ...empty(1, '0'), created: [{ ...incident, typeId: 'incident' }, { ...car, typeId: 'car' }, { ...cam, typeId: 'cam' }], facts: [{ entity: car, fieldId: 'demo.car.speed_mps', value: 5, producer: 'motion', validFrom: at('0') }, { entity: incident, fieldId: 'demo.incident.active', value: false, producer: 'behaviour', validFrom: at('0') }] },
  { ...empty(2, '1000000000'), facts: [{ entity: car, fieldId: 'demo.car.speed_mps', value: 0, producer: 'motion', validFrom: at('1000000000') }] },
  { ...empty(3, '1000000000', 1), predicateTruth: [truth] },
  { ...empty(4, '1000000000', 2), chainInstances: [raise], messages: [message], facts: [{ entity: incident, fieldId: 'demo.incident.active', value: true, producer: 'behaviour', validFrom: at('1000000000', 2) }], events: [{ at_ns: '1000000000', category: 'emergency', severity: 'warning', text: 'Car stopped', entities: ['incident.1'] }] },
  { ...empty(5, '1000000000', 3), chainInstances: [notice] },
];
export const traceStore = () => { const value = new TemporalFeedStore(header); commits.forEach(commit => value.ingest(commit)); value.seek('2000000000'); return value; };

