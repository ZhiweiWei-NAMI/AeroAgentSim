import { afterEach, expect, it, vi } from 'vitest';
import { decodePluginInventory, loadPluginInventory, scenarioPluginForTemplate, type PluginInventory } from './api';

const scenario = {
  name: 'food-delivery', origin: 'builtin', distribution: null, status: 'ok', conflict: [],
  templates: ['food-delivery'], apply_hooks: [],
};
const inventory = {
  api_version: 1,
  scenarios: [scenario],
  engines: [{ name: 'kinematic', origin: 'builtin', distribution: null, status: 'ok', conflict: [] }],
  algorithms: [{ name: 'allocation.nearest', origin: 'entry-point', distribution: 'acme-plugins', status: 'ok', conflict: [] }],
  models: [{ name: 'wind', origin: 'builtin', distribution: null, status: 'ok', conflict: [] }],
};
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('strictly decodes the v1 inventory while ignoring unknown additive fields', () => {
  const decoded = decodePluginInventory({
    ...inventory,
    future_section: { anything: true },
    scenarios: [{ ...scenario, future_field: 1 }],
    engines: [{ ...inventory.engines[0], future_field: 'x' }],
  });
  expect(decoded).toEqual(inventory);
  expect(decoded).not.toHaveProperty('future_section');
  expect(decoded.scenarios[0]).not.toHaveProperty('future_field');
});

it('accepts an older server without status or conflict and reports the row as ok', () => {
  const decoded = decodePluginInventory({
    ...inventory,
    scenarios: [{ name: 'food-delivery', origin: 'builtin', distribution: null, templates: ['food-delivery'], apply_hooks: [] }],
  });
  expect(decoded.scenarios[0]).toEqual({ ...scenario, status: 'ok', conflict: [] });
});

it('keeps duplicate status and every conflicting distribution', () => {
  const decoded = decodePluginInventory({
    ...inventory,
    engines: [{ name: 'kinematic', origin: 'entry-point', distribution: 'acme-plugins', status: 'duplicate', conflict: ['acme-plugins', null, 'other-plugins'] }],
  });
  expect(decoded.engines[0]).toEqual({ name: 'kinematic', origin: 'entry-point', distribution: 'acme-plugins', status: 'duplicate', conflict: ['acme-plugins', null, 'other-plugins'] });
});

it('rejects malformed documents instead of guessing', () => {
  const cases: Array<[string, unknown, RegExp]> = [
    ['a non-object document', null, /invalid document/],
    ['another API version', { ...inventory, api_version: 2 }, /unsupported API version 2/],
    ['a missing catalog', { ...inventory, models: undefined }, /missing its models catalog/],
    ['a missing scenario list', { ...inventory, scenarios: undefined }, /missing its scenario catalog/],
    ['a missing scenario templates list', { ...inventory, scenarios: [{ ...scenario, templates: undefined }] }, /invalid template list/],
    ['a non-string template id', { ...inventory, scenarios: [{ ...scenario, templates: [1] }] }, /invalid template list/],
    ['an unknown origin', { ...inventory, scenarios: [{ ...scenario, origin: 'plugin' }] }, /invalid scenario entry food-delivery origin/],
    ['an unknown status', { ...inventory, scenarios: [{ ...scenario, status: 'broken' }] }, /invalid scenario entry food-delivery status/],
    ['a malformed conflict list', { ...inventory, engines: [{ ...inventory.engines[0], conflict: 'acme' }] }, /invalid .* conflict list/],
    ['a non-string distribution', { ...inventory, engines: [{ ...inventory.engines[0], distribution: 4 }] }, /invalid distribution/],
  ];
  for (const [, value, message] of cases) expect(() => decodePluginInventory(value)).toThrow(message);
});

it('loads the inventory from an explicit API base without throwing', async () => {
  const fetch = vi.fn().mockResolvedValue(response(inventory));
  vi.stubGlobal('fetch', fetch);
  const result = await loadPluginInventory('http://api/');
  expect(fetch).toHaveBeenCalledWith('http://api/v1/plugins', expect.objectContaining({ headers: { Accept: 'application/json' } }));
  expect(result).toEqual({ ok: true, inventory });
});

it('turns an old server 404 into a typed failure', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ detail: 'Not Found' }, 404)));
  expect(await loadPluginInventory('http://api')).toEqual({ ok: false, error: { status: 404, message: 'Not Found' } });
});

it('turns invalid JSON and invalid shapes into typed failures', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not json', { status: 200 })));
  expect(await loadPluginInventory('http://api')).toEqual({ ok: false, error: { message: 'The plugin inventory is not valid JSON.' } });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ ...inventory, api_version: '1' })));
  expect(await loadPluginInventory('http://api')).toEqual({ ok: false, error: { message: 'The plugin inventory uses unsupported API version 1.' } });
});

it('turns a network failure into a typed failure instead of rejecting', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  expect(await loadPluginInventory('http://api')).toEqual({ ok: false, error: { message: 'offline' } });
});

it('maps a template id only when exactly one non-duplicate plugin declares it', () => {
  const decoded: PluginInventory = decodePluginInventory({
    ...inventory,
    scenarios: [
      scenario,
      { name: 'external', origin: 'entry-point', distribution: 'acme-plugins', status: 'ok', templates: ['external-template'], apply_hooks: [] },
    ],
  });
  expect(scenarioPluginForTemplate(decoded, 'food-delivery')?.name).toBe('food-delivery');
  expect(scenarioPluginForTemplate(decoded, 'external-template')?.distribution).toBe('acme-plugins');
  expect(scenarioPluginForTemplate(decoded, 'unknown-template')).toBeUndefined();
  expect(scenarioPluginForTemplate(undefined, 'food-delivery')).toBeUndefined();

  const twice = decodePluginInventory({ ...inventory, scenarios: [scenario, { ...scenario, name: 'second' }] });
  expect(scenarioPluginForTemplate(twice, 'food-delivery')).toBeUndefined();
  const duplicated = decodePluginInventory({ ...inventory, scenarios: [{ ...scenario, status: 'duplicate', conflict: ['acme-plugins', 'other-plugins'] }] });
  expect(scenarioPluginForTemplate(duplicated, 'food-delivery')).toBeUndefined();
  const conflictOnly = decodePluginInventory({ ...inventory, scenarios: [{ ...scenario, conflict: ['acme-plugins'] }] });
  expect(scenarioPluginForTemplate(conflictOnly, 'food-delivery')).toBeUndefined();
  const duplicateName = decodePluginInventory({ ...inventory, scenarios: [scenario, { ...scenario, templates: [] }] });
  expect(scenarioPluginForTemplate(duplicateName, 'food-delivery')).toBeUndefined();
});
