import { afterEach, expect, it, vi } from 'vitest';
import { TransportError } from '../feeds/transport';
import { ApiError, WorkbenchApi, type Project } from './api';

const project = {
  id: 'project', name: 'Project', revision: 8, updated_at: '', dirty: true,
  scenario: { entities: [{ id: 'new-place' }] },
  capabilities: { lowalt: true, region: false, network: false, city: true },
  latest_version: { version: 1, revision: 6, digest: 'saved', created_at: '', validation: { valid: true, revision: 6, errors: [] } },
} satisfies Project & { capabilities: Record<string, boolean> };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each(['place', 'remove', 'knobs', 'import', 'save', 'rename'])('T1-R4-005 / T3-R4-002: %s returns only the authoritative GET, including capabilities and saved version', async operation => {
  let finish!: (value: Response) => void;
  const fetch = vi.fn().mockResolvedValueOnce(response({ id: project.id, revision: project.revision, scenario: {} }))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  vi.stubGlobal('fetch', fetch);
  const api = new WorkbenchApi('http://api');
  let resolved = false;
  const mutation = operation === 'knobs' ? api.knobs('project', { fleet: 3 }, 7)
    : operation === 'import' ? api.importYaml('project', 'run: {}', 7)
    : operation === 'save' ? api.save('project', project.scenario, 7)
    : operation === 'rename' ? api.rename('project', 'Renamed', 7)
    : api.request<Project>(`/v1/studio/workspaces/project/${operation}`, { body: { id: 'new-place', revision: 7 } });
  const completed = mutation.then(value => { resolved = true; return value; });
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(fetch.mock.calls[1][0]).toBe('http://api/v1/studio/workspaces/project');
  expect(fetch.mock.calls[1][1].method).toBe('GET');
  expect(resolved).toBe(false);
  finish(response(project));
  const saved = await completed;
  expect(saved).toEqual(project);
  expect(saved.latest_version?.version).toBe(1);
  expect((saved as typeof project).capabilities.lowalt).toBe(true);
  expect(saved.scenario.entities).toEqual([{ id: 'new-place' }]);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('preserves a removal dry run without fetching or applying a project', async () => {
  const review = { dependencies: [{ kind: 'binding', path: '/bindings/0' }] };
  const fetch = vi.fn().mockResolvedValue(response(review)); vi.stubGlobal('fetch', fetch);
  expect(await new WorkbenchApi('http://api').request('/v1/studio/workspaces/project/remove', { body: { id: 'place', dry_run: true } })).toEqual(review);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('rejects a failed authoritative read instead of returning the successful mutation payload', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response(project)).mockResolvedValueOnce(response({ detail: 'Read failed' }, 503));
  vi.stubGlobal('fetch', fetch);
  await expect(new WorkbenchApi('http://api').knobs('project', { wind: 5 }, 7)).rejects.toEqual(new ApiError(503, 'Read failed'));
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('rejects an invalid mutation visibly without reading a replacement project', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ detail: 'Invalid wind' }, 422)); vi.stubGlobal('fetch', fetch);
  await expect(new WorkbenchApi('http://api').knobs('project', { wind: 'invalid' }, 7)).rejects.toEqual(new ApiError(422, 'Invalid wind'));
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('classifies a protected server without retrying the state-changing POST', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ detail: 'Bearer token required' }, 401)); vi.stubGlobal('fetch', fetch);
  const error = await new WorkbenchApi('http://api').control('run', 'start').catch(value => value);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toBeInstanceOf(TransportError);
  expect(error.status).toBe(401);
  expect(error.kind).toBe('authentication');
  expect(error.en).toContain('local unprotected server');
  expect(error.zh).toContain('未受保护');
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1].method).toBe('POST');
});

it('keeps absent server capabilities typed as unavailable', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ detail: 'not found' }, 404)); vi.stubGlobal('fetch', fetch);
  const error = await new WorkbenchApi('http://api').guide('missing').catch(value => value);
  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(404);
  expect(error.kind).toBe('not_found');
  expect(error.unavailable).toBe(true);
});

it('does not retry a failed mutation', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ detail: 'busy' }, 503)); vi.stubGlobal('fetch', fetch);
  await expect(new WorkbenchApi('http://api').control('run', 'start')).rejects.toThrow('HTTP 503: busy');
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('sends lossless integer tags as raw JSON tokens', async () => {
  const fetch = vi.fn().mockResolvedValue(response({})); vi.stubGlobal('fetch', fetch);
  await new WorkbenchApi('http://api').request('/v1/studio/workspaces/project/validate', { body: { until_ns: { $integer: '9223372036854775815' } } });
  expect(String(fetch.mock.calls[0][1].body)).toContain('"until_ns":9223372036854775815');
});
