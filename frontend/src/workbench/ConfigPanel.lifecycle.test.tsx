import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { ConfigPanel } from './ConfigPanel';
import { ApiError, type Project, type WorkbenchApi } from './api';
import type { SectionProps } from './sections/types';

const sectionState = vi.hoisted(() => ({ broken: false, mutation: undefined as Project | undefined }));
vi.mock('../newcomers/api', () => ({ TemplateApi: class { algorithms = async () => []; } }));
vi.mock('./sections/SpaceSection', () => ({ SpaceSection: (props: SectionProps) => <TestSection id="space" {...props} /> }));
vi.mock('./sections/ObjectsSection', () => ({ ObjectsSection: (props: SectionProps) => <TestSection id="objects" {...props} /> }));
vi.mock('./sections/RunSection', () => ({ RunSection: (props: SectionProps) => <TestSection id="run" {...props} /> }));

function TestSection({ id, ...props }: SectionProps & { id: string }) {
  const [text, setText] = useState('');
  if (id === 'space' && sectionState.broken) throw Error('Map initialization failed');
  return <>
    <input aria-label={`${id} pending form`} value={text} onChange={event => setText(event.target.value)} />
    <button onClick={() => props.edit('/city', undefined)}>Clear city locally</button>
    <button onClick={() => props.edit('/run/seed', 43)}>Edit seed locally</button>
    <button onClick={() => props.onProject(sectionState.mutation!)}>Receive object mutation</button>
    <button onClick={() => props.goTo?.('objects', '/entities/0/facts/lowalt:site')}>Add place jump</button>
    <output data-testid={`${id}-focus`}>{props.focus}</output>
    <output data-testid={`${id}-draft`}>{JSON.stringify(props.draft)}</output>
  </>;
}

const initial: Project = { id: 'project', name: 'Project', revision: 2, updated_at: '', dirty: true, latest_version: null,
  scenario: { city: 'huangpu', entities: [{ id: 'existing', facts: { capacity: 1 } }], run: { seed: 42 } } };
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); sectionState.broken = false; sectionState.mutation = undefined; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function setup(section = 'objects', overrides: Partial<WorkbenchApi> = {}) {
  const onDraft = vi.fn();
  const api = { base: '/api', save: vi.fn().mockImplementation(async (_id, scenario, revision) => ({ ...initial, scenario, revision: revision + 1 })), ...overrides } as unknown as WorkbenchApi;
  function Harness() {
    const [project, setProject] = useState(initial), [where, setWhere] = useState(section);
    return <ConfigPanel api={api} project={project} onProject={setProject} onDraft={onDraft} section={where} onSection={setWhere}
      onInitialize={vi.fn()} initializing={false} onClose={vi.fn()} />;
  }
  render(<I18nProvider><Harness /></I18nProvider>);
  return { api, onDraft };
}

it('T1-007: rebases unsaved edits onto an object mutation and saves against the fresh revision', async () => {
  sectionState.mutation = { ...initial, revision: 4, scenario: { ...initial.scenario, entities: [...initial.scenario.entities, { id: 'qa-point', type: 'aas:Point' }] } };
  const { api, onDraft } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Clear city locally' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit seed locally' }));
  fireEvent.click(screen.getByRole('button', { name: 'Receive object mutation' }));
  const expected = { entities: sectionState.mutation.scenario.entities, run: { seed: 43 } };
  await waitFor(() => expect(onDraft).toHaveBeenLastCalledWith('project', expected, true));
  expect(JSON.parse(sessionStorage.getItem('aas.draft.project')!).revision).toBe(4);
  expect(screen.queryByText(/Unsaved edits were restored/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(api.save).toHaveBeenCalledWith('project', expected, 4));
  await waitFor(() => expect(sessionStorage.getItem('aas.draft.project')).toBeNull());
  expect(screen.queryByTestId('wb-merge')).toBeNull();
});

it('adopts an object flow that already saved the local edits and clears only its own recovery', async () => {
  const { onDraft } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Clear city locally' }));
  sectionState.mutation = { ...initial, revision: 4, scenario: { run: initial.scenario.run, entities: [...initial.scenario.entities, { id: 'qa-point' }] } };
  fireEvent.click(screen.getByRole('button', { name: 'Receive object mutation' }));
  await waitFor(() => expect(onDraft).toHaveBeenLastCalledWith('project', sectionState.mutation!.scenario, false));
  expect(sessionStorage.getItem('aas.draft.project')).toBeNull();
  expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
});

it('T3-R2-005 / T4-R2-010: shows both seed values, explicit unselected choices and the server deletion, then saves the selected draft', async () => {
  const server: Project = { ...initial, revision: 3, scenario: { entities: initial.scenario.entities, run: { seed: 44 } } };
  const save = vi.fn().mockRejectedValueOnce(new ApiError(409, 'revision conflict'))
    .mockImplementation(async (_id, scenario, revision) => ({ ...server, scenario, revision: revision + 1 }));
  setup('run', { save, project: vi.fn().mockResolvedValue(server) });
  fireEvent.click(screen.getByRole('button', { name: 'Edit seed locally' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await screen.findByTestId('wb-merge');
  const run = within(screen.getByTestId('wb-merge-run')), city = within(screen.getByTestId('wb-merge-city'));
  expect(run.getByText('/run/seed')).toBeVisible();
  expect(run.getByText('43')).toBeVisible(); expect(run.getByText('44')).toBeVisible();
  expect(city.getByText('Deleted')).toBeVisible();
  expect(run.getByRole('button', { name: 'Keep mine' })).toHaveAttribute('aria-pressed', 'false');
  expect(run.getByRole('button', { name: 'Take server' })).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByTestId('wb-merge-save')).toBeDisabled();
  fireEvent.click(run.getByRole('button', { name: 'Keep mine' }));
  fireEvent.click(city.getByRole('button', { name: 'Take server' }));
  fireEvent.click(screen.getByTestId('wb-merge-save'));
  await waitFor(() => expect(save).toHaveBeenLastCalledWith('project', { entities: initial.scenario.entities, run: { seed: 43 } }, 3));
  await waitFor(() => expect(screen.queryByTestId('wb-merge')).toBeNull());
  expect(screen.getByTestId('run-draft')).not.toHaveTextContent('huangpu');
});

it('T1-R2-008: contains a section crash, keeps sibling edits and lets Retry recover the section', async () => {
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  sectionState.broken = true;
  setup('run');
  fireEvent.change(screen.getByLabelText('run pending form'), { target: { value: '600' } });
  fireEvent.click(screen.getByRole('button', { name: 'Scene & space' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Map initialization failed');
  expect(screen.getByTestId('wb-config')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Run & recording' })).toBeEnabled();
  sectionState.broken = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByLabelText('space pending form')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Run & recording' }));
  expect(screen.getByLabelText('run pending form')).toHaveValue('600');
  expect(consoleError).toHaveBeenCalled();
});

it('D6: goTo switches to the requested section, preserves the full focus path and leaves prior inputs mounted', async () => {
  setup('run');
  fireEvent.change(screen.getByLabelText('run pending form'), { target: { value: '600' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add place jump' }));
  expect(await screen.findByLabelText('objects pending form')).toBeVisible();
  await waitFor(() => expect(screen.getByTestId('objects-focus')).toHaveTextContent('/entities/0/facts/lowalt:site'));
  fireEvent.click(screen.getByRole('button', { name: 'Run & recording' }));
  expect(screen.getByLabelText('run pending form')).toHaveValue('600');
});
