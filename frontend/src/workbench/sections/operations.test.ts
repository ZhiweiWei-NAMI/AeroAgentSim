import { describe, expect, it, vi } from 'vitest';
import { ApiError, type Project, type WorkbenchApi } from '../api';
import { applyOperations, operation, operationFreshnessKey, operationProblem, operationText, previewOperations } from './operations';

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
