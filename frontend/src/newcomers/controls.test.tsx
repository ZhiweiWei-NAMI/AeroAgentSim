import {afterEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, useLocation} from 'react-router-dom';
import TemplateGallery from './TemplateGallery';
import ExperimentPanel from './ExperimentPanel';
import type {ScenarioTemplate, TemplateKnob} from './api';

Object.defineProperty(window, 'matchMedia', {writable: true, value: vi.fn(() => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}}))});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const template: ScenarioTemplate = {format: 'aeroagentsim.template/v1', id: 'other-template', title: 'Another scenario', summary: 'A real template.', category: 'logistics', difficulty: 'beginner', duration_s: 60, scenario: 'scenario.yaml', observe: [], try: [], kpis: [], knobs: [{id: 'count', label: 'Number of drones', type: 'integer', default: 2, min: 1, max: 8}]};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {status});
function Destination() { const location = useLocation(); return <output data-testid="destination">{location.pathname}{location.search}</output>; }
function chooseNumbers() {
  fireEvent.click(screen.getByTestId('exp-knob-count'));
  for (const value of [2, 4]) {
    fireEvent.change(screen.getByTestId('exp-add-count'), {target: {value: String(value)}});
    fireEvent.click(screen.getByTestId('exp-add-count-button'));
  }
}

it('opens a real workspace for the Studio fallback even when the failed template is not traffic-accident', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/v1/templates')) return response([template]);
    if (url.endsWith('/runs')) return response({detail: 'run service unavailable'}, 503);
    if (url.endsWith('/workspace')) return response({workspace_id: 'workspace-other'});
    return response({}, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter><TemplateGallery apiBase="http://api.test" /><Destination /></MemoryRouter>);
  fireEvent.click(await screen.findByTestId('run-template-other-template'));
  const fallback = await screen.findByRole('button', {name: 'Try in Studio instead'});
  await waitFor(() => expect(fallback).toBeEnabled());
  fireEvent.click(fallback);
  await waitFor(() => expect(screen.getByTestId('destination')).toHaveTextContent('/studio?workspace=workspace-other'));
  const workspaceRequest = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/v1/templates/other-template/workspace'));
  expect(workspaceRequest?.[1]?.method).toBe('POST');
  expect(JSON.parse(String(workspaceRequest?.[1]?.body))).toEqual({knobs: {}});
});

it('prevents invalid, duplicate and excess numeric experiment values including keyboard Enter', () => {
  render(<ExperimentPanel base="http://api.test" templates={[template]} />);
  fireEvent.click(screen.getByTestId('exp-knob-count'));
  const input = screen.getByTestId('exp-add-count');
  fireEvent.change(input, {target: {value: '99'}});
  expect(screen.getByTestId('exp-add-count-button')).toBeDisabled();
  fireEvent.keyDown(input, {key: 'Enter', code: 'Enter'});
  // InputNumber may normalize the draft to its maximum on Enter, but the
  // out-of-bounds draft must not be submitted as an experiment value.
  expect(screen.queryByTestId('exp-remove-count-99')).toBeNull();
  for (const value of [1, 2, 3, 4, 5]) {
    fireEvent.change(input, {target: {value: String(value)}});
    fireEvent.click(screen.getByTestId('exp-add-count-button'));
    if (value === 1) {
      fireEvent.change(input, {target: {value: '1'}});
      expect(screen.getByTestId('exp-add-count-button')).toBeDisabled();
      fireEvent.keyDown(input, {key: 'Enter', code: 'Enter'});
      expect(screen.getAllByTestId('exp-remove-count-1')).toHaveLength(1);
    }
  }
  expect(input).toBeDisabled();
  expect(screen.getByText('Up to 5 values per setting. Remove one to add another.')).toBeInTheDocument();
  fireEvent.click(screen.getByTestId('exp-remove-count-1'));
  expect(input).toBeEnabled();
});

it('keeps number and string choice values distinct instead of finding both by their string representation', async () => {
  const choice: TemplateKnob = {id: 'mixed', label: 'Mixed typed choices', type: 'choice', default: '1', options: ['1', 1]};
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response({id: 'exp', contract: 'aeroagentsim.experiment/v1', template_id: template.id, template_title: template.title, status: 'completed', seeds: 3, jobs: 1, horizon_s: null, knobs: {}, vary: {mixed: ['1', 1]}, total: 6, completed: 6, failed: 0, error: null, runs: [], table: []}));
  vi.stubGlobal('fetch', fetchMock);
  render(<ExperimentPanel base="http://api.test" templates={[{...template, knobs: [choice]}]} />);
  fireEvent.click(screen.getByTestId('exp-knob-mixed'));
  const select = screen.getByRole('combobox', {name: 'Add a value for Mixed typed choices'});
  fireEvent.mouseDown(select);
  fireEvent.click(document.querySelectorAll('.ant-select-item-option')[0]);
  fireEvent.mouseDown(select);
  fireEvent.click(document.querySelectorAll('.ant-select-item-option')[1]);
  fireEvent.click(screen.getByTestId('experiment-start'));
  await screen.findByTestId('experiment-report');
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).vary.mixed).toEqual(['1', 1]);
});

it('recovers failed progress polling by fetching the same experiment, without creating another one', async () => {
  const report = {id: 'exp', contract: 'aeroagentsim.experiment/v1', template_id: template.id, template_title: template.title, status: 'running', seeds: 3, jobs: 1, horizon_s: null, knobs: {}, vary: {count: [2, 4]}, total: 6, completed: 0, failed: 0, error: null, runs: [], table: []};
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response(report));
  fetchMock.mockResolvedValueOnce(response(report)).mockResolvedValueOnce(response({detail: 'network unavailable'}, 503)).mockResolvedValueOnce(response({...report, status: 'completed', completed: 6}));
  vi.stubGlobal('fetch', fetchMock);
  render(<ExperimentPanel base="http://api.test" templates={[template]} />);
  chooseNumbers();
  vi.useFakeTimers();
  await act(async () => { fireEvent.click(screen.getByTestId('experiment-start')); });
  expect(screen.getByTestId('experiment-report')).toHaveTextContent('Running — 0 of 6 runs finished.');
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByTestId('experiment-error')).toHaveTextContent('could not be followed');
  await act(async () => { fireEvent.click(screen.getByTestId('experiment-retry-progress')); });
  expect(screen.queryByTestId('experiment-error')).toBeNull();
  expect(screen.getByTestId('experiment-report')).toHaveTextContent('Completed — 6 of 6 runs succeeded.');
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  expect(String(fetchMock.mock.calls[2][0])).toBe('http://api.test/v1/experiments/exp');
});
