import { describe, expect, it, vi } from 'vitest';
import { ApiError, type Project, type WorkbenchApi } from '../api';
import { applyOperations, assertOperationsReady, currentOperationsProject, getOperationsState, mutateWorkspace, OperationStateError, operation, operationFreshnessKey, operationProblem, operationText, previewOperations, subscribeOperations } from './operations';

const project = { id: 'workspace/1', revision: 7 } as Project;

describe('operation review inputs (T1-R2-005 / T2-R2-001 / T3-R2-004 / T4-R2-002)', () => {
  it('ignores request IDs and sorts nested object keys', () => {
    const first = operation('set_cooperation', { watchers: { vehicles: ['uav-1', 'uav-2'], notice_radius_m: 150 } });
    const second = operation('set_cooperation', { watchers: { notice_radius_m: 150, vehicles: ['uav-1', 'uav-2'] } });
    expect(first.id).not.toBe(second.id);
    expect(operationFreshnessKey([first], 7)).toBe(operationFreshnessKey([second], 7));
  });

  it('invalidates on a parameter, operation, array order or base revision change', () => {
    const first = [operation('set_cooperation', { watchers: { vehicles: ['u1', 'u2'], notice_radius_m: 150 } })];
    const key = operationFreshnessKey(first, 7);
    expect(operationFreshnessKey([operation('set_cooperation', { watchers: { vehicles: ['u1', 'u2'], notice_radius_m: 151 } })], 7)).not.toBe(key);
    expect(operationFreshnessKey([operation('set_cooperation', { watchers: { vehicles: ['u2', 'u1'], notice_radius_m: 150 } })], 7)).not.toBe(key);
    expect(operationFreshnessKey([{ ...first[0], op: 'remove_cooperation' }], 7)).not.toBe(key);
    expect(operationFreshnessKey(first, 8)).not.toBe(key);
  });

  it('keeps the reviewed request and revision in the transport and refreshes the applied server state', async () => {
    const server = { ...project, revision: 8 };
    const preview = { diff: { added: [], changed: [], removed: [] }, notes: [] };
    const api = { request: vi.fn().mockResolvedValueOnce(preview).mockResolvedValueOnce(server), project: vi.fn().mockResolvedValue(server) };
    const operations = [operation('set_duration', { seconds: 1.5 })];
    expect(await previewOperations(api as unknown as WorkbenchApi, project, operations)).toBe(preview);
    expect(api.request.mock.calls[0]).toEqual(['/v1/studio/workspaces/workspace%2F1/operations', { body: { operations, revision: 7, dry_run: true } }]);
    expect(await applyOperations(api as unknown as WorkbenchApi, project, operations)).toBe(server);
    expect(api.request.mock.calls[1][1].body).toEqual({ operations, revision: 7 });
    expect(api.project).toHaveBeenCalledWith(project.id);
  });

  it('does not report success or load an invented project when apply fails', async () => {
    const failure = new ApiError(422, JSON.stringify({ operation: 'form-1', field: 'seconds', message: 'Invalid duration / 时长无效' }));
    const api = { request: vi.fn().mockRejectedValue(failure), project: vi.fn() };
    await expect(applyOperations(api as unknown as WorkbenchApi, project, [operation('set_duration', { seconds: -1 })])).rejects.toBe(failure);
    expect(api.project).not.toHaveBeenCalled();
    expect(operationProblem(failure)).toMatchObject({ field: 'seconds' });
  });
});

it('selects one language from C11 messages while preserving units and paths (T4-R2-014)', () => {
  const message = 'Kitchen k has no meal orders; keep 2 m/s at /config/profiles / 厨房 k 没有餐食订单，请保留 2 m/s';
  expect(operationText(message, false)).toBe('Kitchen k has no meal orders; keep 2 m/s at /config/profiles');
  expect(operationText(message, true)).toBe('厨房 k 没有餐食订单，请保留 2 m/s');
  expect(operationText('2 m/s', false)).toBe('2 m/s');
});

it('locks a workspace through POST and the following GET, publishes the new revision, and rejects old confirmations locally (T2-R3-006 / T3-R3-002 / T4-R3-004)', async () => {
  const base = { ...project, id: 'serial-workspace' }, updated = { ...base, revision: 8 };
  let finishRead!: (value: Project) => void;
  const api = { request: vi.fn().mockResolvedValue(updated), project: vi.fn(() => new Promise<Project>(resolve => { finishRead = resolve; })) };
  const snapshots: Array<{ applying: boolean; revision?: number }> = [];
  const unsubscribe = subscribeOperations(base.id, () => { const state = getOperationsState(base.id); snapshots.push({ applying: state.applying, revision: state.project?.revision }); });
  try {
    const edits = [operation('set_wind', { speed_mps: 3 })];
    const applying = applyOperations(api as unknown as WorkbenchApi, base, edits);
    expect(getOperationsState(base.id).applying).toBe(true);
    await vi.waitFor(() => expect(api.project).toHaveBeenCalledOnce());
    expect(() => previewOperations(api as unknown as WorkbenchApi, base, [operation('add_gust', { start_s: 10, end_s: 20, speed_mps: 5 })])).toThrow(OperationStateError);
    await expect(applyOperations(api as unknown as WorkbenchApi, base, edits)).rejects.toMatchObject({ reason: 'applying' });
    expect(api.request).toHaveBeenCalledOnce();
    finishRead(updated);
    expect(await applying).toBe(updated);
    expect(snapshots).toEqual([{ applying: true }, { applying: true, revision: 8 }, { applying: false, revision: 8 }]);
    expect(currentOperationsProject(base)).toBe(updated);
    await expect(applyOperations(api as unknown as WorkbenchApi, base, edits)).rejects.toMatchObject({ reason: 'stale' });
    expect(api.request).toHaveBeenCalledOnce();
    expect(operationProblem(new OperationStateError('stale')).message).not.toContain('elsewhere');
  } finally { unsubscribe(); }
});

it('releases the shared gate after a rejected operation and keeps the real failure', async () => {
  const base = { ...project, id: 'rejected-workspace' };
  const failure = new ApiError(422, JSON.stringify({ operation: 'invalid', message: 'Invalid wind / 风速无效' }));
  const api = { request: vi.fn().mockRejectedValue(failure), project: vi.fn() };
  const unsubscribe = subscribeOperations(base.id, () => undefined);
  try {
    await expect(applyOperations(api as unknown as WorkbenchApi, base, [operation('set_wind', { speed_mps: -1 })])).rejects.toBe(failure);
    expect(getOperationsState(base.id)).toEqual({ applying: false });
    expect(api.project).not.toHaveBeenCalled();
  } finally { unsubscribe(); }
});

it('allows independent workspaces to apply without sharing a global lock', async () => {
  const bases = ['a', 'b'].map(id => ({ ...project, id }));
  const resolvers = new Map<string, (value: Project) => void>();
  const api = { request: vi.fn().mockResolvedValue({}), project: vi.fn((id: string) => new Promise<Project>(resolve => resolvers.set(id, resolve))) };
  const applying = bases.map(base => applyOperations(api as unknown as WorkbenchApi, base, [operation('set_duration', { seconds: 600 })]));
  await vi.waitFor(() => expect(resolvers.size).toBe(2));
  expect(bases.map(base => getOperationsState(base.id).applying)).toEqual([true, true]);
  for (const base of bases) resolvers.get(base.id)!({ ...base, revision: 8 });
  await Promise.all(applying);
  expect(bases.map(base => getOperationsState(base.id).applying)).toEqual([false, false]);
});

it('composes ConfigPanel draft saving without opening the gate before the mutation/refresh (T1-R4-003 / T4-R4-002)', async () => {
  const base = { ...project, id: 'composite' }, saved = { ...base, revision: 8 }, updated = { ...base, revision: 9 };
  let finishSave!: (value: Project) => void, finishRead!: (value: Project) => void;
  const snapshots: boolean[] = [];
  const unsubscribe = subscribeOperations(base.id, () => snapshots.push(getOperationsState(base.id).applying));
  const prepare = () => {
    assertOperationsReady(currentOperationsProject(base)); // the actual saveDraft entry check
    return mutateWorkspace(base, () => new Promise<Project>(resolve => { finishSave = resolve; }));
  };
  const apply = vi.fn((prepared: Pick<Project, 'id' | 'revision'>) => {
    expect(prepared).toBe(saved);
    expect(getOperationsState(base.id).applying).toBe(true);
    return new Promise<Project>(resolve => { finishRead = resolve; });
  });
  try {
    const pending = mutateWorkspace(base, apply, prepare);
    await expect(mutateWorkspace(base, vi.fn())).rejects.toMatchObject({ reason: 'applying' });
    expect(apply).not.toHaveBeenCalled();
    finishSave(saved);
    await vi.waitFor(() => expect(apply).toHaveBeenCalledOnce());
    expect(() => previewOperations({ request: vi.fn() } as unknown as WorkbenchApi, saved, [operation('set_wind', { speed_mps: 3 })])).toThrow(OperationStateError);
    expect(snapshots.every(Boolean)).toBe(true);
    finishRead(updated);
    expect(await pending).toBe(updated);
    expect(snapshots.filter(value => !value)).toHaveLength(1);
    expect(currentOperationsProject(base)).toBe(updated);
  } finally { unsubscribe(); }
});

it('keeps a successful save authoritative when the following operation fails; no async caller can borrow the nested save permission', async () => {
  const base = { ...project, id: 'failed-composite' }, saved = { ...base, revision: 8 };
  const unsubscribe = subscribeOperations(base.id, () => undefined);
  const failure = new Error('Real mutation failure');
  try {
    await expect(mutateWorkspace(base, async () => { throw failure; }, async () => saved)).rejects.toBe(failure);
    expect(getOperationsState(base.id)).toEqual({ applying: false, project: saved });
    await expect(mutateWorkspace(saved, vi.fn(), async () => {
      await Promise.resolve();
      return mutateWorkspace(saved, async () => saved);
    })).rejects.toMatchObject({ reason: 'applying' });
    expect(getOperationsState(base.id).applying).toBe(false);
  } finally { unsubscribe(); }
});

it('never runs the mutation when saving prerequisites fails', async () => {
  const apply = vi.fn();
  await expect(mutateWorkspace({ ...project, id: 'unsaved-composite' }, apply, async () => undefined)).rejects.toThrow(/could not be saved/);
  expect(apply).not.toHaveBeenCalled();
  expect(getOperationsState('unsaved-composite').applying).toBe(false);
});
