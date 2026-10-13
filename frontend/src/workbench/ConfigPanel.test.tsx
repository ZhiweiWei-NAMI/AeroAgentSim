import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ConfigPanel, controlSource, controlValue, issuePointer, issueSection } from './ConfigPanel';
import { ApiError, type Project, WorkbenchApi } from './api';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import { within } from '@testing-library/react';
const catalog = vi.hoisted(() => ({ template: vi.fn(), algorithms: vi.fn() }));
vi.mock('../newcomers/api', async original => ({ ...await original<object>(), TemplateApi: class { template = catalog.template; algorithms = catalog.algorithms; } }));
vi.mock('../studio/BehavioursPanel', () => ({ BehavioursPanel: () => null }));
const project: Project = { id: 'test-project', name: 'Project', revision: 2, updated_at: '', dirty: true, latest_version: null, scenario: { entities: [{ id: 'restaurant', type: 'lowalt:Restaurant', facts: { capacity: 2 } }], run: { until_ns: 360000000000 } } };
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); catalog.algorithms.mockResolvedValue([]); Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView'); });
function LanguageSwitch() { const { locale, setLocale } = useI18n(); return <button onClick={()=>setLocale(locale==='zh-CN'?'en-US':'zh-CN')}>Switch language</button>; }
function setup(initial = project, section = 'run', extra: Record<string, unknown> = {}) {
  const api = { base: '/api', save: vi.fn().mockImplementation(async (_id, scenario, revision) => ({ ...initial, scenario, revision: revision + 1 })), exportYaml: vi.fn().mockResolvedValue('run:\n  until_ns: 360000000000\n'), validate: vi.fn(), ...extra } as unknown as WorkbenchApi;
  const onInitialize = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(initial), [where, setWhere] = useState(section);
    return <ConfigPanel api={api} project={draft} onProject={setDraft} section={where} onSection={setWhere} onInitialize={onInitialize} initializing={false} onClose={vi.fn()} />;
  }
  const view = render(<I18nProvider><LanguageSwitch/><Harness /></I18nProvider>); return { api, view, onInitialize };
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
it('reads compound physics frequency from actual engine periods, preserving the draft',()=>{
 const knob={id:'physics_hz',label:'Physics updates per second',type:'number' as const,default:1,apply:'traffic.physics_hz'};
 const scenario={run:{advance_ns:1e9},engines:{road_motion:{config:{step_ns:66666667}},air_motion:{config:{step_ns:66666667}}}};
 expect(controlSource(knob,scenario,{physics_hz:1})).toBe(15);expect(scenario.engines.road_motion.config.step_ns).toBe(66666667);
 expect(controlSource(knob,{engines:{road_motion:{config:{step_ns:1e9}},air_motion:{config:{step_ns:1e9}}}},{physics_hz:15})).toBe(1);
 expect(controlSource(knob,{engines:{road_motion:{config:{step_ns:1e9}},air_motion:{config:{step_ns:5e8}}}},{physics_hz:1})).toBeUndefined();
});

const trafficWind = (value: unknown = [[5, 2]], vectorIndex: number | null = 0) => ({
  engines: {
    air_motion: { plugin: 'kinematic', config: { consumption_model: { plugin: 'wind', config: { entity: 'wind.region', field: 'oo:digital_twin.wind.horizontalVelocity', ...(vectorIndex === null ? {} : { vector_index: vectorIndex }) } } } },
    weather: { plugin: 'environment', config: { profiles: { 'wind.region': { 'oo:digital_twin.wind.horizontalVelocity': { mode: 'constant', value } } } } },
  },
});

it('T2-R4-004 / D5: reads both traffic wind components from the runtime consumer, including tagged numbers and a flat vector', () => {
  const east = { id: 'wind_east_mps', label: 'Eastward wind (m/s)', type: 'number' as const, default: 3, apply: 'traffic.wind_east' };
  const north = { ...east, id: 'wind_north_mps', apply: 'traffic.wind_north' };
  expect(controlSource(east, trafficWind(), { wind_east_mps: 3 })).toBe(5);
  expect(controlSource(north, trafficWind(), { wind_north_mps: 0 })).toBe(2);
  expect(controlSource(east, trafficWind([[{ $number: '5.0' }, 2]]), { wind_east_mps: 3 })).toBe(5);
  expect(controlSource(north, trafficWind([5, 2, 0], null), { wind_north_mps: 0 })).toBe(2);
  for (const value of [{}, null, [[null, 2]], [[5]], [['invalid', 2]]]) {
    expect(controlSource(east, trafficWind(value), { wind_east_mps: 3 })).toBeUndefined();
  }
  expect(controlSource(east, {}, { wind_east_mps: 3 })).toBeUndefined();
});

it('T2-R4-004: displays 5 m/s after generic edits and sends only the unrelated edited control', async () => {
  catalog.template.mockResolvedValue({ id: 'traffic', knobs: [
    { id: 'wind_east_mps', label: 'Eastward wind (m/s)', type: 'number', default: 3, apply: 'traffic.wind_east' },
    { id: 'accident_time_s', label: 'Accident time (seconds)', type: 'number', default: 20, pointer: '/accident_time_s' },
  ] });
  const initial = { ...project, template: { id: 'traffic', knobs: { wind_east_mps: 3, accident_time_s: 20 } }, scenario: { ...trafficWind(), accident_time_s: 20 } };
  const knobs = vi.fn().mockResolvedValue({ ...initial, revision: 3, scenario: { ...initial.scenario, accident_time_s: 25 } });
  setup(initial, 'controls', { knobs });
  const wind = await screen.findByRole('spinbutton', { name: 'Eastward wind (m/s)' });
  expect(Number((wind as HTMLInputElement).value)).toBe(5);
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Accident time (seconds)' }), { target: { value: '25' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply controls to draft' }));
  await screen.findByText('Controls applied to the draft.');
  expect(knobs).toHaveBeenCalledWith(initial.id, { accident_time_s: 25 }, initial.revision);
  expect(Number((wind as HTMLInputElement).value)).toBe(5);
});

it('T3-R4-002: partial knob responses cannot hide existing place and fleet forms or the saved version', async () => {
  catalog.template.mockResolvedValue({ id: 'example', knobs: [{ id: 'rate', label: 'Rate', type: 'number', pointer: '/rate', default: 1 }] });
  const initial = { ...project, template: { id: 'example', knobs: { rate: 1 } }, capabilities: { lowalt: true, region: false, network: false, city: false },
    latest_version: { version: 1, revision: 2, digest: 'saved', created_at: '', validation: { valid: true, revision: 2, errors: [] } }, scenario: { entities: [], rate: 1 } };
  const fresh = { ...initial, revision: 3, scenario: { entities: [], rate: 2 } };
  let read!: (value: Response) => void;
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ id: initial.id, revision: 3, scenario: fresh.scenario })))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { read = resolve; }));
  vi.stubGlobal('fetch', fetch);
  const api = new WorkbenchApi('/api');
  function Harness() {
    const [current, setCurrent] = useState<Project>(initial), [section, setSection] = useState('controls');
    return <ConfigPanel api={api} project={current} onProject={setCurrent} section={section} onSection={setSection} onClose={vi.fn()} onInitialize={vi.fn()} initializing={false} />;
  }
  render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Rate' }), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply controls to draft' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('button', { name: 'Applying controls…' })).toBeDisabled();
  expect(screen.queryByText('Controls applied to the draft.')).toBeNull();
  await act(async () => read(new Response(JSON.stringify(fresh))));
  await screen.findByText('Controls applied to the draft.');
  expect(screen.getByTestId('wb-initialize')).toHaveTextContent('Initialize v1');
  fireEvent.click(screen.getByRole('button', { name: 'Objects' }));
  expect(await screen.findByRole('button', { name: 'Add place' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add fleet' })).toBeVisible();
  expect(screen.queryByText('Start with standard low-altitude components')).toBeNull();
});

it('T1-R4-005: advanced creation keeps the real saved version and installed components after a partial placement response', async () => {
  const initial = { ...project, capabilities: { lowalt: true, region: false, network: false, city: false },
    latest_version: { version: 1, revision: 2, digest: 'saved', created_at: '', validation: { valid: true, revision: 2, errors: [] } } };
  let server: Project & { capabilities: typeof initial.capabilities } = initial;
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), 'http://api').pathname;
    if (path === '/api/v1/studio/types') return new Response(JSON.stringify({ types: [{ id: 'aas:StudioFacility', parents: [], abstract: false, kind: 'facility', engine: 'workflow' }], fields: [] }));
    if (path === '/api/v1/studio/types/aas%3AStudioFacility') return new Response(JSON.stringify({ id: 'aas:StudioFacility', parents: [], abstract: false, fields: [] }));
    if (path === '/api/v1/studio/workspaces/test-project/place' && options?.method === 'POST') {
      const body = JSON.parse(String(options.body));
      server = { ...initial, revision: 3, scenario: { ...initial.scenario, entities: [...initial.scenario.entities, { id: body.id, type: body.type, facts: {} }] } };
      return new Response(JSON.stringify({ id: server.id, revision: server.revision, scenario: server.scenario }));
    }
    if (path === '/api/v1/studio/workspaces/test-project' && options?.method === 'GET') return new Response(JSON.stringify(server));
    throw Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal('fetch', fetch);
  const api = new WorkbenchApi('/api');
  function Harness() {
    const [current, setCurrent] = useState<Project>(initial);
    return <ConfigPanel api={api} project={current} onProject={setCurrent} section="objects" onSection={vi.fn()} onClose={vi.fn()} onInitialize={vi.fn()} initializing={false} />;
  }
  render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Add objects (advanced)' }));
  fireEvent.click(await screen.findByRole('option', { name: /aas:StudioFacility/ }));
  fireEvent.change(await screen.findByLabelText('id (or id prefix)'), { target: { value: 'research-static' } });
  const create = screen.getByRole('button', { name: 'Create 1 objects' });
  await waitFor(() => expect(create).toBeEnabled()); fireEvent.click(create);
  await waitFor(() => expect(screen.getByText('Draft revision 3')).toBeVisible());
  expect(screen.getByTestId('wb-initialize')).toHaveTextContent('Initialize v1');
  expect(screen.getByText('latest version v1 (earlier revision)', { exact: false })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add place' })).toBeVisible(); expect(screen.getByRole('button', { name: 'Add fleet' })).toBeVisible();
  expect(screen.queryByText('Start with standard low-altitude components')).toBeNull();
  expect(server.scenario.entities).toEqual(expect.arrayContaining([expect.objectContaining({ id: 'research-static' })]));
});
it('jumps a validation issue to its field even when the objects filter excludes it', async () => {
  setup({ ...project, validation: { valid: false, revision: 2, errors: ['bad'], issues: [{ source: 'scenario', path: '/entities/0/facts/capacity', section: 'objects', message: 'bad' }] } }, 'objects');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Filter instances' }), { target: { value: 'absent' } });
  fireEvent.click(screen.getByTestId('wb-issues').querySelector('button')!);
  expect(await screen.findByLabelText('capacity')).toBeVisible();
  expect(screen.getByLabelText('capacity').closest('.wb-field')).toHaveClass('is-focus');
});
it('blocks saving and validation while a numeric input is invalid', async () => {
  const { api } = setup();
  fireEvent.change(await screen.findByRole('textbox', { name: /until_ns/ }), { target: { value: '' } });
  fireEvent.blur(screen.getByRole('textbox', { name: /until_ns/ }));
  await waitFor(() => expect(screen.getByTestId('wb-validate')).toBeDisabled());
  expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled();
});

it('recovers an unapplied duration on refresh, reports unconfirmed input, and leaves the server draft unchanged (T3-R3-003 / T4-R2-009)', async () => {
  const first = setup();
  fireEvent.change(await screen.findByLabelText('Duration (s)'), { target: { value: '777' } });
  expect(screen.getByText('Unconfirmed form inputs')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
  expect(screen.getByRole('textbox', { name: /until_ns/ })).toHaveValue('360000000000');
  expect(first.api.save).not.toHaveBeenCalled();
  first.view.unmount();
  const second = setup();
  expect(screen.getByLabelText('Duration (s)')).toHaveValue('777');
  expect(screen.getByText('Restored unsaved inputs')).toBeVisible();
  expect(screen.getByRole('textbox', { name: /until_ns/ })).toHaveValue('360000000000');
  expect(second.api.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
  expect(screen.getByLabelText('Duration (s)')).toHaveValue('360');
  expect(screen.queryByText('Unconfirmed form inputs')).toBeNull();
});
it('recovers edits against an older revision without deleting them', async () => {
  sessionStorage.setItem('aas.draft.test-project', JSON.stringify({ revision: 1, scenario: { run: { until_ns: 123 } } }));
  setup();
  expect(await screen.findByRole('textbox', { name: /until_ns/ })).toHaveValue('123');
  expect(sessionStorage.getItem('aas.draft.test-project')).toContain('123');
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
it('locks template controls during application so a pending response cannot discard a new edit',async()=>{
 catalog.template.mockResolvedValue({id:'example',knobs:[{id:'fleet',label:'Fleet count',type:'integer',pointer:'/generators/fleet/count',default:8}]});
 const initial={...project,template:{id:'example',knobs:{fleet:8}},scenario:{generators:{fleet:{count:8}}}};
 let finish!:(value:Project)=>void;const knobs=vi.fn(()=>new Promise<Project>(resolve=>{finish=resolve;}));setup(initial,'controls',{knobs});
 const input=await screen.findByRole('spinbutton',{name:'Fleet count'});fireEvent.change(input,{target:{value:'9'}});fireEvent.click(screen.getByRole('button',{name:'Apply controls to draft'}));
 await waitFor(()=>expect(input).toBeDisabled());expect(screen.getByRole('button',{name:'Applying controls…'})).toBeDisabled();
 await act(async()=>{finish({...initial,revision:3,scenario:{generators:{fleet:{count:9}}}});});expect(input).toBeEnabled();expect(input).toHaveValue('9');
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
  fireEvent.blur(input);
  await waitFor(() => expect(screen.queryByText('Revision 2 is valid. Save it as a version to run it.')).toBeNull());
  expect(screen.getByTestId('wb-save-version')).toBeDisabled();
});
it('edits large engine geometry without mounting thousands of hidden inputs or dropping coordinates', async () => {
  const config = { scene: { points: Array.from({ length: 1000 }, (_, i) => [i, i + 0.5, 0]) } };
  const initial = { ...project, scenario: { engines: { capture: { plugin: 'capture', config } } } };
  const { api, view } = setup(initial, 'components');
  const advanced = await screen.findByText('Advanced', { selector: 'summary' });
  expect(view.container.querySelectorAll('input')).toHaveLength(0);
  fireEvent.click(advanced);
  fireEvent.click(screen.getByText('capture', { selector: 'b' }));
  const editor = screen.getByLabelText('capture configuration');
  const changed = { scene: { points: [...config.scene.points] } }; changed.scene.points[0] = [9, 0.5, 0];
  fireEvent.change(editor, { target: { value: JSON.stringify(changed) } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply JSON', hidden: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(api.save).toHaveBeenCalled());
  const scenario = vi.mocked(api.save).mock.calls[0][1] as any;
  expect(scenario.engines.capture.config.scene.points).toEqual(changed.scene.points);
});
it('publishes unsaved coordinate edits for preview without saving or initializing', async () => {
 const onDraft = vi.fn(), save = vi.fn();
 const initial = { ...project, scenario: { ...project.scenario, entities: [{ id: 'drone', type: 'lowalt:UAV', facts: { 'lowalt.position': [10, 20, 30] } }] } };
 render(<ConfigPanel api={{ base: '/api', save } as unknown as WorkbenchApi} project={initial} onProject={vi.fn()} onDraft={onDraft} onInitialize={vi.fn()} initializing={false} section="objects" onSection={vi.fn()} onClose={vi.fn()} />);
 fireEvent.click(within(document.querySelector('[data-path="/entities/0"]')!).getByRole('button', { name: 'Edit' }));
 const coordinate = screen.getByLabelText('east (m)');
 fireEvent.change(coordinate, { target: { value: '120' } });
 fireEvent.blur(coordinate);
 await waitFor(() => expect(onDraft).toHaveBeenLastCalledWith(initial.id, expect.objectContaining({ entities: [expect.objectContaining({ facts: { 'lowalt.position': [120, 20, 30] } })] }), true));
 expect(save).not.toHaveBeenCalled();
});
it('translates the saved control notice and template title when language changes, keeping edited values',async()=>{
 catalog.template.mockResolvedValue({id:'example',title:'World',title_zh:'世界',knobs:[{id:'fleet',label:'Fleet count',label_zh:'数量',type:'integer',pointer:'/generators/fleet/count',default:8}]});
 const initial={...project,name:'World',template:{id:'example',knobs:{fleet:8}},scenario:{generators:{fleet:{count:8}}}};
 const knobs=vi.fn().mockResolvedValue({...initial,revision:3,scenario:{generators:{fleet:{count:9}}}});setup(initial,'controls',{knobs});
 fireEvent.change(await screen.findByRole('spinbutton',{name:'Fleet count'}),{target:{value:'9'}});fireEvent.click(screen.getByRole('button',{name:'Apply controls to draft'}));await screen.findByText('Controls applied to the draft.');
 fireEvent.click(screen.getByRole('button',{name:'Switch language'}));expect(screen.getByText('控制项已应用到草稿。')).toBeVisible();expect(screen.getByRole('heading',{name:'配置 · 世界'})).toBeVisible();expect(screen.getByRole('spinbutton',{name:'数量'})).toHaveValue('9');expect(knobs).toHaveBeenCalledTimes(1);
});

it('a 409 save opens Compare and merge: local edits stay, choices are per section, and the merge saves on the server revision', async () => {
  const serverProject: Project = { ...project, revision: 5, scenario: { ...project.scenario, run: { until_ns: 999 } } };
  const api409 = { base: '/api', save: vi.fn()
    .mockRejectedValueOnce(new ApiError(409, 'revision conflict'))
    .mockImplementation(async (_id: string, scenario: Record<string, unknown>, revision: number) => ({ ...project, scenario, revision })),
    project: vi.fn().mockResolvedValue(serverProject) } as unknown as WorkbenchApi;
  function Harness409() {
    const [current, setCurrent] = useState(project);
    return <ConfigPanel api={api409} project={current} onProject={setCurrent} section="run" onSection={vi.fn()} onInitialize={vi.fn()} initializing={false} onClose={vi.fn()} />;
  }
  render(<I18nProvider><Harness409 /></I18nProvider>);
  fireEvent.change(await screen.findByRole('textbox', { name: /until_ns/ }), { target: { value: '777' } });
  fireEvent.blur(screen.getByRole('textbox', { name: /until_ns/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  const merge = await screen.findByTestId('wb-merge');
  expect(merge).toHaveTextContent('Compare and merge');
  expect(merge).toHaveTextContent('run');
  // Local edits are never discarded: the until_ns field still shows 777.
  expect(screen.getByRole('textbox', { name: /until_ns/ })).toHaveValue('777');
  // Saving is blocked until every changed section has a choice.
  expect(screen.getByTestId('wb-merge-save')).toBeDisabled();
  fireEvent.click(within(screen.getByTestId('wb-merge')).getByRole('button', { name: 'Take server' }));
  fireEvent.click(screen.getByTestId('wb-merge-save'));
  await waitFor(() => expect(api409.save).toHaveBeenCalledTimes(2));
  expect(api409.save).toHaveBeenLastCalledWith('test-project', expect.objectContaining({ run: { until_ns: 999 } }), 5);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
  expect(document.querySelector('.wb-config-state-copy')?.textContent).toMatch(/Merged draft saved as revision \d+\./);
  expect(screen.queryByTestId('wb-merge')).toBeNull();
});

it('an invalid saved draft can still be exported and repaired in the file editor while form edits are kept', async () => {
  setup({ ...project, validation: { valid: false, revision: 2, errors: ['run.until_ns must be positive'] } }, 'file');
  fireEvent.click(screen.getByRole('button', { name: 'Load scenario file' }));
  const yaml = await screen.findByRole('textbox', { name: 'Scenario YAML' });
  expect(yaml).toBeVisible();
  expect(screen.getByRole('button', { name: 'Download YAML' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Reload' })).toBeEnabled();
  // The export shows the saved draft even while unsaved form edits exist.
  expect(yaml).toHaveValue('run:\n  until_ns: 360000000000\n');
});

it.each(['save record', 'project summary'])('T4-03: reloads the authoritative project after a %s response and keeps v1 available', async responseShape => {
  const version = { version: 1, revision: 2, digest: 'v1', created_at: '', validation: { valid: true, revision: 2, errors: [] } };
  const initial: Project = { ...project, latest_version: version, dirty: false };
  let server = initial;
  const api = new WorkbenchApi('/api');
  const request = vi.spyOn(api, 'request').mockImplementation(async (_path, init) => {
    if (init?.body) {
      const body = init.body as { scenario: Project['scenario']; revision: number };
      server = { ...initial, scenario: body.scenario, revision: body.revision + 1, dirty: true };
      return responseShape === 'save record' ? { id: server.id, scenario: server.scenario, revision: server.revision } : server;
    }
    return server;
  });
  const initialize = vi.fn();
  function Harness() {
    const [current, setCurrent] = useState(initial);
    return <ConfigPanel api={api} project={current} onProject={setCurrent} section="run" onSection={vi.fn()} onInitialize={initialize} initializing={false} onClose={vi.fn()} />;
  }
  render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.change(await screen.findByRole('textbox', { name: /until_ns/ }), { target: { value: '600000000000' } });
  fireEvent.blur(screen.getByRole('textbox', { name: /until_ns/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(screen.getByTestId('wb-initialize')).toHaveTextContent('Initialize v1'));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled());
  expect(request).toHaveBeenCalledTimes(2);
  expect(request).toHaveBeenLastCalledWith('/v1/studio/workspaces/test-project', { signal: undefined });
  expect(screen.getByText(/latest version v1 \(earlier revision\)/)).toBeVisible();
  expect(screen.getByTestId('wb-initialize')).toBeEnabled();
  fireEvent.click(screen.getByTestId('wb-initialize'));
  expect(initialize).toHaveBeenCalledWith(1);
});

it('T4-01: another tab cannot clear this tab’s recovery, and a conflicted edit survives refresh', async () => {
  const initial = { ...project, scenario: { ...project.scenario, run: { until_ns: 360000000000, seed: 46 } } };
  const server = { ...initial, revision: 3, scenario: { ...initial.scenario, run: { until_ns: 360000000000, seed: 48 } } };
  const { view } = setup(initial, 'run', { save: vi.fn().mockRejectedValue(new ApiError(409, 'revision conflict')), project: vi.fn().mockResolvedValue(server) });
  fireEvent.click(screen.getByText('Advanced (raw tree)', { selector: 'summary' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'seed' }), { target: { value: '47' } });
  fireEvent.blur(screen.getByRole('textbox', { name: 'seed' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await screen.findByTestId('wb-merge');
  const key = 'aas.draft.test-project';
  expect(sessionStorage.getItem(key)).toContain('47');
  // A different browsing context changes shared localStorage, not this tab's sessionStorage.
  localStorage.setItem(key, JSON.stringify({ revision: 3, scenario: server.scenario }));
  localStorage.removeItem(key);
  view.unmount();
  setup(server);
  expect(await screen.findByLabelText('Random seed')).toHaveValue('47');
  expect(screen.getByText('Unsaved edits were restored from this browser.')).toBeVisible();
  expect(sessionStorage.getItem(key)).toContain('47');
  expect(screen.getByText(/The server draft changed/)).toBeVisible();
});

it('a successful save clears only this tab’s buffer', async () => {
  localStorage.setItem('aas.draft.test-project', 'other tab recovery');
  setup();
  fireEvent.change(await screen.findByRole('textbox', { name: /until_ns/ }), { target: { value: '600000000000' } });
  fireEvent.blur(screen.getByRole('textbox', { name: /until_ns/ }));
  expect(sessionStorage.getItem('aas.draft.test-project')).toContain('600000000000');
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(sessionStorage.getItem('aas.draft.test-project')).toBeNull());
  expect(localStorage.getItem('aas.draft.test-project')).toBe('other tab recovery');
});

it('T4-R2-009: keeps the real Run duration form and unconfirmed JSON mounted across section switches', async () => {
  const { api } = setup({ ...project, scenario: { run: { until_ns: 2000000000, seed: 42 } } });
  const duration = screen.getByLabelText('Duration (s)');
  fireEvent.change(duration, { target: { value: '600' } });
  fireEvent.click(screen.getByRole('button', { name: 'Objects' }));
  expect(duration).not.toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Run & recording' }));
  expect(screen.getByLabelText('Duration (s)')).toBe(duration);
  expect(duration).toHaveValue('600');
  expect(api.save).not.toHaveBeenCalled();
});
