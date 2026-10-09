import { expect, it } from 'vitest';
import { validateCommit, validateHeader } from './http';
import { TemporalFeedStore } from './temporal-store';
import { fixtureCommits, fixtureHeader, truth } from '../behaviours/extension-fixture';
it('decodes extensions and retains same-ns microstep truth and chain states at a frozen cut', () => {
  const store = new TemporalFeedStore(validateHeader(fixtureHeader));
  fixtureCommits.slice(0,3).forEach(row=>store.ingest(validateCommit(row)));
  store.seek('10',2); store.select({id:'task-1',generation:1});
  expect(store.predicateTruth.get(truth.contextId)?.value).toBeNull();
  expect(store.chainInstances.get('response/task-1')?.state).toBe('waiting');
  expect(store.selection).toEqual({runId:'fixture-kernel-run',epoch:'fixture-epoch',id:'task-1',generation:1});
  store.ingest(validateCommit(fixtureCommits[3])); store.seek('10',2);
  expect(store.predicateTruth.get(truth.contextId)?.value).toBeNull();
  store.seek('10',3); expect(store.predicateTruth.get(truth.contextId)?.value).toBe(true);
  expect(store.chainInstances.get('response/task-1')?.state).toBe('moving');
  store.seek('9007199254740993',4); expect(store.chainInstances.get('response/task-1')?.revision).toEqual({$integer:'9007199254740993'});
  store.seek('0',1); expect(store.predicateTruth.get(truth.contextId)?.value).toBe(false);
});
it('closes an interval only at its actual knowledge version and never resurrects the assertion', () => {
  const store = new TemporalFeedStore(fixtureHeader); fixtureCommits.slice(0,2).forEach(row=>store.ingest(row));
  store.seek('10',1); expect(store.predicateTruth.get(truth.contextId)?.value).toBe(false);
  store.seek('10',2); expect(store.predicateTruth.get(truth.contextId)?.value).toBeNull();
});
it('rejects invalid truth, absence of diagnostics, unsafe revisions and invalid identity', () => {
  for (const patch of [{status:'known',value:null}, {status:'required_input',value:null,diagnostics:[]}, {roles:{task:{id:'task',generation:-1}}}]) expect(()=>validateCommit({...fixtureCommits[0],predicateTruth:[{...truth,...patch}]})).toThrow();
  expect(()=>validateCommit({...fixtureCommits[0],chainInstances:[{...fixtureCommits[0].chainInstances![0],revision:9007199254740992}]})).toThrow();
  const legacy = new TemporalFeedStore({...fixtureHeader,epoch:undefined}); expect(()=>legacy.select({id:'task',generation:0})).toThrow('epoch');
});
