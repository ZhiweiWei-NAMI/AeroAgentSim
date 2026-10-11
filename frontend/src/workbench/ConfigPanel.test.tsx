import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ConfigPanel, controlValue, issuePointer, issueSection } from './ConfigPanel';
import type { Project, WorkbenchApi } from './api';
const catalog = vi.hoisted(() => ({ template: vi.fn(), algorithms: vi.fn() }));
vi.mock('../newcomers/api', async original => ({ ...await original<object>(), TemplateApi: class { template = catalog.template; algorithms = catalog.algorithms; } }));
vi.mock('../studio/BehavioursPanel', () => ({ BehavioursPanel: () => null }));
const project: Project = { id: 'test-project', name: 'Project', revision: 2, updated_at: '', dirty: true, latest_version: null, scenario: { entities: [{ id: 'restaurant', type: 'lowalt:Restaurant', facts: { capacity: 2 } }], run: { until_ns: 360000000000 } } };
beforeEach(() => { localStorage.clear(); catalog.algorithms.mockResolvedValue([]); Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true }); });
afterEach(() => { cleanup(); Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView'); });
function setup(initial = project, section = 'run', extra: Record<string, unknown> = {}) {
  const api = { base: '/api', save: vi.fn().mockImplementation(async (_id, scenario, revision) => ({ ...initial, scenario, revision: revision + 1 })), exportYaml: vi.fn().mockResolvedValue('run:\n  until_ns: 360000000000\n'), validate: vi.fn(), ...extra } as unknown as WorkbenchApi;
  const onInitialize = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(initial), [where, setWhere] = useState(section);
    return <ConfigPanel api={api} project={draft} onProject={setDraft} section={where} onSection={setWhere} onInitialize={onInitialize} initializing={false} onClose={vi.fn()} />;
  }
  const view = render(<Harness />); return { api, view, onInitialize };
}
it('accepts wire pointers and escapes authored path tokens', () => {
  expect(issuePointer('/entities/0/facts/lowalt.capacity')).toBe('/entities/0/facts/lowalt.capacity');
  expect(issuePointer('$.engines.motion.config.speed_mps')).toBe('/engines/motion/config/speed_mps');
  expect(issueSection({ source: 'scenario', path: '/run/until_ns', message: 'bad' })).toBe('run');
});
it('reads tagged rule parameters for numeric controls without defaulting missing values', () => {
  expect(controlValue({ $number: '12.0' })).toBe(12);
  expect(controlValue({ $integer: '42' })).toBe(42);
  expect(controlValue(undefined)).toBeUndefined(); expect(controlValue(null)).toBeUndefined();
});
it('jumps a validation issue to its field even when the objects filter excludes it', async () => {
  setup({ ...project, validation: { valid: false, revision: 2, errors: ['bad'], issues: [{ source: 'scenario', path: '/entities/0/facts/capacity', section: 'objects', message: 'bad' }] } }, 'objects');
  fireEvent.change(screen.getByRole('textbox', { name: 'Filter instances' }), { target: { value: 'absent' } });
  fireEvent.click(screen.getByTestId('wb-issues').querySelector('button')!);
  expect(await screen.findByLabelText('capacity')).toBeVisible();
  expect(screen.getByLabelText('capacity').closest('.wb-field')).toHaveClass('is-focus');
});
it('blocks saving and validation while a numeric input is invalid', async () => {
  const { api } = setup();
  fireEvent.change(screen.getByRole('textbox', { name: /until_ns/ }), { target: { value: '' } });
  await waitFor(() => expect(screen.getByTestId('wb-validate')).toBeDisabled());
  expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled();
});
it('recovers edits against an older revision without deleting them', async () => {
  localStorage.setItem('aas.draft.test-project', JSON.stringify({ revision: 1, scenario: { run: { until_ns: 123 } } }));
  setup();
  expect(await screen.findByRole('textbox', { name: /until_ns/ })).toHaveValue('123');
  expect(localStorage.getItem('aas.draft.test-project')).toContain('123');
  expect(screen.getByText(/The server draft changed/)).toBeVisible();
});
it('reads controls from the actual draft and applies only the changed knob', async () => {
  catalog.template.mockResolvedValue({ id: 'example', knobs: [{ id: 'fleet', label: 'Fleet count', type: 'integer', pointer: '/generators/fleet/count', default: 8 }, { id: 'rate', label: 'Rate', type: 'number', pointer: '/rate', default: 1 }] });
  const initial = { ...project, template: { id: 'example', knobs: { fleet: 8, rate: 1 } }, scenario: { generators: { fleet: { count: 12 } }, rate: 3 } };
  const knobs = vi.fn().mockResolvedValue({ ...initial, revision: 3 }); setup(initial, 'controls', { knobs });
  const input = await screen.findByRole('spinbutton', { name: 'Fleet count' });
  expect(input).toHaveValue('12'); fireEvent.change(input, { target: { value: '13' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply controls to draft' }));
  await waitFor(() => expect(knobs).toHaveBeenCalledWith(initial.id, { fleet: 13 }, initial.revision));
});
it('shows file loading and blocks conflicting actions until the real export arrives', async () => {
  let finish!: (text: string) => void;
  const exportYaml = vi.fn().mockImplementation(() => new Promise<string>(resolve => { finish = resolve; }));
  setup(project, 'file', { exportYaml });
  fireEvent.click(screen.getByRole('button', { name: 'Load scenario file' }));
  expect(screen.getByRole('status')).toHaveTextContent('Loading the saved scenario file');
  expect(screen.getByRole('button', { name: 'Loading scenario file…' })).toBeDisabled();
  expect(screen.getByTestId('wb-validate')).toBeDisabled();
  await act(async () => { finish('run:\n  seed: 42\n'); });
  expect(screen.getByRole('textbox', { name: 'Scenario YAML' })).toHaveValue('run:\n  seed: 42\n');
  expect(screen.queryByRole('status')).toBeNull();
  expect(screen.getByTestId('wb-validate')).toBeEnabled();
});
it('clears the old field error highlight when the revision validates successfully', async () => {
  const initial = { ...project, validation: { valid: false, revision: 2, errors: ['bad'], issues: [{ source: 'scenario', path: '/entities/0/facts/capacity', section: 'objects' as const, message: 'bad' }] } };
  setup(initial, 'objects', { validate: vi.fn().mockResolvedValue({ valid: true, revision: 2 }), project: vi.fn().mockResolvedValue({ ...initial, validation: { valid: true, revision: 2, errors: [] } }) });
  fireEvent.click(screen.getByTestId('wb-issues').querySelector('button')!);
  const input = await screen.findByLabelText('capacity'); expect(input.closest('.wb-field')).toHaveClass('is-focus');
  fireEvent.click(screen.getByTestId('wb-validate'));
  await waitFor(() => expect(input.closest('.wb-field')).not.toHaveClass('is-focus'));
  expect(screen.queryByTestId('wb-issues')).toBeNull();
  expect(screen.getByText('Revision 2 is valid. Save it as a version to run it.')).toBeVisible();
  fireEvent.change(input, { target: { value: '3' } });
  await waitFor(() => expect(screen.queryByText('Revision 2 is valid. Save it as a version to run it.')).toBeNull());
  expect(screen.getByTestId('wb-save-version')).toBeDisabled();
});
it('edits large engine geometry without mounting thousands of hidden inputs or dropping coordinates', async () => {
  const config = { scene: { points: Array.from({ length: 1000 }, (_, i) => [i, i + 0.5, 0]) } };
  const initial = { ...project, scenario: { engines: { capture: { plugin: 'capture', config } } } };
  const { api, view } = setup(initial, 'components');
  expect(view.container.querySelectorAll('input')).toHaveLength(0);
  const editor = screen.getByLabelText('capture config');
  const changed = { scene: { points: [...config.scene.points] } }; changed.scene.points[0] = [9, 0.5, 0];
  fireEvent.change(editor, { target: { value: JSON.stringify(changed) } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply JSON', hidden: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(api.save).toHaveBeenCalled());
  const scenario = vi.mocked(api.save).mock.calls[0][1] as any;
  expect(scenario.engines.capture.config.scene.points).toEqual(changed.scene.points);
});
